package com.shanpu.module.seckill.service.impl;

import cn.hutool.core.lang.Snowflake;
import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.domain.vo.OrderDetailVO;
import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.domain.vo.SeckillGrabVO;
import com.shanpu.module.seckill.infra.SeckillProducer;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import com.shanpu.module.seckill.service.SeckillService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 秒杀业务服务实现。
 *
 * <p>本类负责统一抢购入口的活动校验与档位路由：普惠档走 MySQL 同步事务，
 * 限时档走 Redis 预扣后同步落库，超级档走 Redis 预扣后写入 Kafka 异步落库。</p>
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class SeckillServiceImpl implements SeckillService {

    private static final Duration ACTIVITY_TTL = Duration.ofMinutes(10);
    private static final Duration ACTIVITY_NULL_TTL = Duration.ofMinutes(2);
    private static final Duration ORDER_PENDING_TTL = Duration.ofSeconds(30);
    private static final String MSG_INVALID_SECKILL_TIER = "非法秒杀档位";
    private static final String MSG_DUPLICATE_ACTIVITY = "该活动您已参与";
    private static final String MSG_SOLD_OUT = "已售罄";
    private static final String MSG_ACTIVITY_NOT_WARMED = "秒杀活动尚未预热";
    private static final String MSG_INVALID_ACTIVITY_STATUS = "非法活动状态";

    private final SeckillActivityMapper activityMapper;
    private final CacheClient cacheClient;
    private final SeckillRedisExecutor redisExecutor;
    private final SeckillProducer seckillProducer;
    private final SeckillOrderWriter orderWriter;
    private final Snowflake snowflake;

    /**
     * 查询秒杀活动列表，并按活动档位补齐当前库存展示值。
     *
     * @param tier 可选档位过滤
     * @param status 可选状态过滤，取值为 UPCOMING、ACTIVE、ENDED
     * @return 活动列表
     */
    @Override
    public List<SeckillActivityVO> list(Integer tier, String status) {
        Integer statusCode = toStatusCode(status);
        return activityMapper.selectList(tier, statusCode).stream()
                .map(this::withCurrentStock)
                .toList();
    }

    /**
     * 查询秒杀活动详情。
     *
     * <p>详情优先命中 Caffeine L1，未命中时读取 Redis L2 的逻辑过期数据，
     * 两层都未命中才回源数据库。返回前用 Redis 秒杀库存覆盖展示库存，保证页面看到
     * 的库存接近抢购脚本使用的库存视角。</p>
     *
     * @param activityId 秒杀活动 ID
     * @return 活动详情
     */
    @Override
    public SeckillActivityVO detail(Long activityId) {
        // 活动详情是抢购前置热点 Key，CacheClient 会自动按 L1 -> L2 -> DB 顺序读取。
        SeckillActivityVO activity = cacheClient.queryWithNullCache(
                RedisKeys.seckillActivityNull(activityId),
                () -> cacheClient.queryWithLogicalExpire(
                        RedisKeys.SECKILL_ACTIVITY_PREFIX,
                        activityId,
                        SeckillActivityVO.class,
                        activityMapper::selectDetailById,
                        ACTIVITY_TTL
                ),
                ACTIVITY_NULL_TTL
        );
        if (activity == null) {
            // 不存在的活动会写入短 TTL 空值占位，拦截重复非法 ID 查询。
            throw new BusinessException(ResultCode.NOT_FOUND);
        }
        // 详情缓存保存活动静态信息，展示库存以 Redis 当前库存为准。
        return withCurrentStock(activity);
    }

    /**
     * 统一抢购入口。
     *
     * <p>方法先通过活动详情二级缓存读取活动、校验时间窗口并生成订单号，再根据
     * 活动 tier 路由到普惠、限时或超级秒杀链路。限时与超级链路会先执行
     * Redis + Lua 预扣减。</p>
     *
     * @param userId 当前登录用户 ID
     * @param activityId 秒杀活动 ID
     * @return 抢购结果，限时成功为 PAID，超级成功为 PENDING
     */
    @Override
    public SeckillGrabVO grab(Long userId, Long activityId) {
        // 抢购链路只读活动详情，不读本地库存；库存扣减仍由 DB 或 Redis Lua 保证一致性。
        SeckillActivityVO activity = detail(activityId);
        validateOpen(activity);

        String orderId = snowflake.nextIdStr();
        return switch (activity.getTier()) {
            case SeckillActivity.TIER_INCLUSIVE -> grabInclusive(userId, orderId, activity);
            case SeckillActivity.TIER_FLASH -> grabFlash(userId, orderId, activity);
            case SeckillActivity.TIER_SUPER -> grabSuper(userId, orderId, activity);
            default -> throw new BusinessException(ResultCode.PARAM_INVALID, MSG_INVALID_SECKILL_TIER);
        };
    }

    /**
     * 执行普惠档抢购。
     *
     * <p>普惠档完全依赖 MySQL 行锁和唯一索引保证库存与一人一单；
     * Redis 库存脚本服务限时和超级两档。</p>
     *
     * @param userId 当前登录用户 ID
     * @param orderId 预生成订单号
     * @param activity 活动详情
     * @return 已支付订单结果
     */
    private SeckillGrabVO grabInclusive(Long userId, String orderId, SeckillActivityVO activity) {
        orderWriter.createSyncPaidOrder(
                userId,
                orderId,
                activity.getId(),
                activity.getProductId(),
                activity.getSeckillPrice(),
                Order.SOURCE_SECKILL_INCLUSIVE
        );
        return paid(orderId, activity.getTier());
    }

    /**
     * 执行限时档抢购。
     *
     * <p>限时档先通过 Lua 原子预扣 Redis 库存并写入已购集合，再同步写 MySQL 订单。
     * 数据库事务失败时撤销 Redis 预扣减，让已购标记和库存恢复到预扣前状态。</p>
     *
     * @param userId 当前登录用户 ID
     * @param orderId 预生成订单号
     * @param activity 活动详情
     * @return 已支付订单结果
     */
    private SeckillGrabVO grabFlash(Long userId, String orderId, SeckillActivityVO activity) {
        // Redis 先行拦截无库存和重复购买，降低进入数据库事务的并发量。
        preDeductOrThrow(activity.getId(), userId, orderId);
        try {
            orderWriter.createSyncPaidOrder(
                    userId,
                    orderId,
                    activity.getId(),
                    activity.getProductId(),
                    activity.getSeckillPrice(),
                    Order.SOURCE_SECKILL_FLASH
            );
            return paid(orderId, activity.getTier());
        } catch (RuntimeException e) {
            // DB 扣库存、扣积分或写订单失败时，撤销 Redis 中的库存与已购状态。
            redisExecutor.rollback(activity.getId(), userId);
            throw e;
        }
    }

    /**
     * 执行超级档抢购。
     *
     * <p>超级档先通过 Lua 完成 Redis 预扣减，再把订单写入 Kafka 等待异步消费。
     * 生产消息失败时回滚 Redis 预扣减，释放已经失去落库机会的库存名额。</p>
     *
     * @param userId 当前登录用户 ID
     * @param orderId 预生成订单号
     * @param activity 活动详情
     * @return 待确认订单结果
     */
    private SeckillGrabVO grabSuper(Long userId, String orderId, SeckillActivityVO activity) {
        // Redis 预扣成功表示该用户拿到一个异步落库名额。
        preDeductOrThrow(activity.getId(), userId, orderId);
        LocalDateTime createdAt = LocalDateTime.now();
        // 消息体保存落库所需的业务快照，消费时不再依赖活动档位的实时值。
        SeckillOrderMsg msg = SeckillOrderMsg.builder()
                .orderId(orderId)
                .userId(userId)
                .activityId(activity.getId())
                .productId(activity.getProductId())
                .price(activity.getSeckillPrice())
                .source(Order.SOURCE_SECKILL_SUPER)
                .createdAt(createdAt)
                .build();
        try {
            // Producer 拿到 Kafka ack 后再返回 PENDING，确保后续消费端有机会落库。
            seckillProducer.send(msg);
            cachePendingOrderDetail(userId, orderId, activity, createdAt);
            return SeckillGrabVO.builder()
                    .orderId(orderId)
                    .status("PENDING")
                    .tier(activity.getTier())
                    .build();
        } catch (RuntimeException e) {
            // Kafka 提交失败时订单消息停在生产前，需要释放 Redis 预扣库存和已购标记。
            redisExecutor.rollback(activity.getId(), userId);
            throw e;
        }
    }

    /**
     * 写入超级档订单待确认占位，避免前端轮询在异步落库窗口内重建空值缓存。
     */
    private void cachePendingOrderDetail(Long userId, String orderId, SeckillActivityVO activity, LocalDateTime createdAt) {
        OrderDetailVO pending = OrderDetailVO.builder()
                .orderId(orderId)
                .productId(activity.getProductId())
                .productTitle(activity.getProductTitle())
                .coverUrl(activity.getCoverUrl())
                .price(activity.getSeckillPrice())
                .status("PENDING")
                .source(Order.SOURCE_SECKILL_SUPER)
                .createdAt(createdAt)
                .build();
        try {
            cacheClient.set(RedisKeys.orderDetail(userId, orderId), pending, ORDER_PENDING_TTL);
        } catch (RuntimeException e) {
            log.warn("超级档订单待确认缓存写入失败: orderId={}, error={}: {}",
                    orderId, e.getClass().getSimpleName(), e.getMessage());
        }
    }

    /**
     * 执行 Redis + Lua 预扣减，并将脚本返回码转换为业务异常。
     *
     * <p>活动未预热时会回源数据库尝试补一次 Redis 库存 Key，再重试 Lua 脚本。
     * 该兜底只用于修复预热缺失，并维持库存扣减和一人一单的原子性。</p>
     *
     * @param activityId 秒杀活动 ID
     * @param userId 当前登录用户 ID
     * @param orderId 预生成订单号
     */
    private void preDeductOrThrow(Long activityId, Long userId, String orderId) {
        Long result = redisExecutor.preDeduct(activityId, userId, orderId);
        if (Long.valueOf(SeckillRedisExecutor.OK).equals(result)) {
            return;
        }
        // Lua 已经在 Redis 内完成一人一单判断，这里只把返回码转换成接口业务错误。
        if (Long.valueOf(SeckillRedisExecutor.DUPLICATE).equals(result)) {
            throw new BusinessException(ResultCode.DUPLICATE_ORDER, MSG_DUPLICATE_ACTIVITY);
        }
        if (Long.valueOf(SeckillRedisExecutor.NOT_WARMED).equals(result)) {
            SeckillActivityVO activity = activityMapper.selectDetailById(activityId);
            if (activity != null) {
                // 预热缺失时只补建 Redis Key，然后重新执行同一份 Lua 判断。
                redisExecutor.ensureStock(activity);
                Long retry = redisExecutor.preDeduct(activityId, userId, orderId);
                if (Long.valueOf(SeckillRedisExecutor.OK).equals(retry)) {
                    return;
                }
                result = retry;
            }
        }
        if (Long.valueOf(SeckillRedisExecutor.DUPLICATE).equals(result)) {
            throw new BusinessException(ResultCode.DUPLICATE_ORDER, MSG_DUPLICATE_ACTIVITY);
        }
        if (Long.valueOf(SeckillRedisExecutor.STOCK_SHORTAGE).equals(result)) {
            throw new BusinessException(ResultCode.STOCK_SHORTAGE, MSG_SOLD_OUT);
        }
        throw new BusinessException(ResultCode.INTERNAL_ERROR, MSG_ACTIVITY_NOT_WARMED);
    }

    /**
     * 构造同步抢购成功响应。
     *
     * @param orderId 订单号
     * @param tier 活动档位
     * @return 已支付抢购结果
     */
    private SeckillGrabVO paid(String orderId, Integer tier) {
        return SeckillGrabVO.builder()
                .orderId(orderId)
                .status("PAID")
                .tier(tier)
                .build();
    }

    /**
     * 校验活动是否处于可抢购时间窗口。
     *
     * @param activity 活动详情
     */
    private void validateOpen(SeckillActivityVO activity) {
        LocalDateTime now = LocalDateTime.now();
        if (activity.getStartAt() != null && activity.getStartAt().isAfter(now)) {
            throw new BusinessException(ResultCode.ACTIVITY_NOT_START);
        }
        if (activity.getEndAt() != null && !activity.getEndAt().isAfter(now)) {
            throw new BusinessException(ResultCode.ACTIVITY_ENDED);
        }
        if (activity.getStatusCode() != null && activity.getStatusCode() == SeckillActivity.STATUS_ENDED) {
            throw new BusinessException(ResultCode.ACTIVITY_ENDED);
        }
    }

    /**
     * 用 Redis 当前库存覆盖活动展示库存。
     *
     * @param activity 活动详情
     * @return 带当前库存的活动详情
     */
    private SeckillActivityVO withCurrentStock(SeckillActivityVO activity) {
        activity.setStock(redisExecutor.currentStock(activity));
        return activity;
    }

    /**
     * 将接口层状态字符串转换为数据库状态码。
     *
     * @param status 状态字符串
     * @return 数据库状态码，未传时返回 null
     */
    private Integer toStatusCode(String status) {
        if (status == null || status.isBlank()) {
            return null;
        }
        return switch (status) {
            case "UPCOMING" -> SeckillActivity.STATUS_UPCOMING;
            case "ACTIVE" -> SeckillActivity.STATUS_ACTIVE;
            case "ENDED" -> SeckillActivity.STATUS_ENDED;
            default -> throw new BusinessException(ResultCode.PARAM_INVALID, MSG_INVALID_ACTIVITY_STATUS);
        };
    }
}
