package com.shanpu.module.seckill.infra;

import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 秒杀 Redis 脚本执行器。
 *
 * <p>限时档和超级档都通过本组件调用 Lua 脚本完成库存预扣、一人一单判断、
 * 失败回滚和库存展示值读取。</p>
 */
@Component
@RequiredArgsConstructor
public class SeckillRedisExecutor {

    /**
     * Lua 执行成功。
     */
    public static final long OK = 0L;

    /**
     * Redis 库存不足。
     */
    public static final long STOCK_SHORTAGE = 1L;

    /**
     * 用户已在该活动购买过。
     */
    public static final long DUPLICATE = 2L;

    /**
     * 活动库存 Key 不存在，表示活动未预热或 Redis 状态丢失。
     */
    public static final long NOT_WARMED = 3L;

    private final StringRedisTemplate redisTemplate;
    private final DefaultRedisScript<Long> seckillScript;
    private final DefaultRedisScript<Long> seckillRollbackScript;

    /**
     * 执行秒杀预扣减 Lua 脚本。
     *
     * <p>脚本在 Redis 单线程内一次性完成活动预热检查、库存检查、一人一单检查、
     * 库存递减和已购集合写入，返回值由 {@link #OK}、{@link #STOCK_SHORTAGE}、
     * {@link #DUPLICATE}、{@link #NOT_WARMED} 表示。</p>
     *
     * @param activityId 秒杀活动 ID
     * @param userId 当前抢购用户 ID
     * @param orderId 本次抢购预生成订单号
     * @return Lua 脚本业务返回码
     */
    public Long preDeduct(Long activityId, Long userId, String orderId) {
        return redisTemplate.execute(
                seckillScript,
                List.of(RedisKeys.seckillStock(activityId), RedisKeys.seckillBought(activityId)),
                String.valueOf(userId),
                orderId
        );
    }

    /**
     * 回滚一次已经成功的 Redis 预扣减。
     *
     * <p>回滚脚本先从已购集合移除用户，只有移除成功才回补库存，
     * 因此同一个用户的库存最多回补一次。</p>
     *
     * @param activityId 秒杀活动 ID
     * @param userId 需要撤销预扣减的用户 ID
     */
    public void rollback(Long activityId, Long userId) {
        redisTemplate.execute(
                seckillRollbackScript,
                List.of(RedisKeys.seckillStock(activityId), RedisKeys.seckillBought(activityId)),
                String.valueOf(userId)
        );
    }

    /**
     * 为限时和超级两档活动准备 Redis 库存 Key 与已购集合 TTL。
     *
     * <p>普惠档使用 MySQL 库存链路，本方法只为限时和超级两档准备 Redis 库存层。
     * 库存 Key 使用 set-if-absent，保持线上扣减结果的权威性。</p>
     *
     * @param activity 活动详情
     */
    public void ensureStock(SeckillActivityVO activity) {
        if (activity.getTier() == null || activity.getTier() == SeckillActivity.TIER_INCLUSIVE) {
            return;
        }
        Duration ttl = redisTtl(activity.getEndAt());
        // SETNX 只在 Key 不存在时写入库存，保留线上扣减后的实时库存。
        redisTemplate.opsForValue().setIfAbsent(
                RedisKeys.seckillStock(activity.getId()),
                String.valueOf(activity.getTotalStock()),
                ttl
        );
        // Redis 空 Set 无法单独设置 TTL，写入哨兵成员后再设置过期时间。
        redisTemplate.opsForSet().add(RedisKeys.seckillBought(activity.getId()), "__warmup__");
        redisTemplate.expire(RedisKeys.seckillBought(activity.getId()), ttl);
    }

    /**
     * 读取活动当前库存展示值。
     *
     * <p>限时和超级档优先读 Redis 库存，普惠档、未预热或 Redis 值异常时使用
     * 数据库活动库存，保证列表和详情接口始终有可展示的库存。</p>
     *
     * @param activity 活动详情
     * @return 当前可展示库存
     */
    public Integer currentStock(SeckillActivityVO activity) {
        if (activity.getTier() == null || activity.getTier() == SeckillActivity.TIER_INCLUSIVE) {
            return activity.getTotalStock();
        }
        String value = redisTemplate.opsForValue().get(RedisKeys.seckillStock(activity.getId()));
        if (value == null || value.isBlank()) {
            return activity.getTotalStock();
        }
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException e) {
            // 库存 Key 被人工写坏时，读接口继续使用数据库库存，后续对账任务会修正 Redis 值。
            return activity.getTotalStock();
        }
    }

    /**
     * 直接设置 Redis 秒杀库存。
     *
     * <p>该方法供库存对账任务在发现 Redis 库存高于数据库剩余库存时收敛差异。</p>
     *
     * @param activityId 秒杀活动 ID
     * @param stock 需要写入 Redis 的库存值
     */
    public void setStock(Long activityId, int stock) {
        redisTemplate.opsForValue().set(RedisKeys.seckillStock(activityId), String.valueOf(stock));
    }

    /**
     * 计算秒杀库存与已购集合的 Redis TTL。
     *
     * <p>库存状态保留到活动结束后一小时，方便异步消费、超时回补和短时间对账。</p>
     *
     * @param endAt 活动结束时间
     * @return Redis Key 过期时间
     */
    private Duration redisTtl(LocalDateTime endAt) {
        if (endAt == null) {
            return Duration.ofHours(2);
        }
        Duration ttl = Duration.between(LocalDateTime.now(), endAt.plusHours(1));
        return !ttl.isNegative() && !ttl.isZero() ? ttl : Duration.ofHours(1);
    }
}
