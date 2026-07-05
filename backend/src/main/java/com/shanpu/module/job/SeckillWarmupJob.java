package com.shanpu.module.job;

import com.shanpu.cache.CacheClient;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 秒杀活动预热任务。
 *
 * <p>任务负责提前写入活动详情逻辑过期缓存，并为限时、超级两档准备 Redis 库存。
 * 活动详情通过 {@link CacheClient#setWithLogicalExpire(String, Object, Duration)} 写入，
 * Redis 作为 L2 保存完整数据，同进程内的 Caffeine L1 会同步尝试回填，后续活动详情
 * 查询和抢购前置校验可优先命中本地缓存。</p>
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class SeckillWarmupJob {

    private static final String JOB_NAME = "seckill-warmup";
    private static final Duration ACTIVITY_TTL = Duration.ofMinutes(10);

    private final SeckillActivityMapper activityMapper;
    private final CacheClient cacheClient;
    private final SeckillRedisExecutor redisExecutor;

    /**
     * 定时预热即将开始的秒杀活动。
     *
     * <p>调度频率由配置控制，默认每分钟执行一次。预热范围由 {@link #runOnce()}
     * 统一处理，保证定时任务和管理接口手动触发使用同一套缓存写入逻辑。</p>
     */
    @Scheduled(cron = "${shanpu.seckill.warmup.cron:0 */1 * * * *}")
    public void warmupUpcoming() {
        runOnce();
    }

    /**
     * 执行一轮活动预热。
     *
     * <p>任务扫描未来 60 分钟内需要预热的活动，写入活动详情逻辑过期缓存；
     * 限时和超级两档同时准备 Redis 库存与已购集合。活动详情缓存写入后会同步尝试
     * 回填活动 L1，降低活动开始瞬间对 Redis 单 Key 的读压力。</p>
     *
     * @return 本轮任务执行统计
     */
    public JobRunVO runOnce() {
        LocalDateTime now = LocalDateTime.now();
        List<SeckillActivityVO> activities =
                activityMapper.selectWarmupList(now, now.plusMinutes(60));
        int succeeded = 0;
        int failed = 0;
        for (SeckillActivityVO activity : activities) {
            if (warmupOne(activity)) {
                succeeded++;
            } else {
                failed++;
            }
        }
        return JobRunVO.of(JOB_NAME, activities.size(), succeeded, failed);
    }

    /**
     * 手动预热单个秒杀活动。
     *
     * <p>用于档位热切换或运维修复缓存时补建活动详情与库存 Key。</p>
     *
     * @param activityId 活动 ID
     */
    public void warmupOne(Long activityId) {
        SeckillActivityVO activity = activityMapper.selectDetailById(activityId);
        if (activity != null) {
            warmupOne(activity);
        }
    }

    /**
     * 预热单个活动的活动详情缓存和 Redis 秒杀库存。
     *
     * <p>活动详情三档都写入逻辑过期缓存；库存 Key 只对限时和超级两档有意义，
     * 具体判断由 {@link SeckillRedisExecutor#ensureStock(SeckillActivityVO)} 负责。</p>
     *
     * @param activity 活动详情
     * @return true 表示预热成功，false 表示预热失败且已记录日志
     */
    private boolean warmupOne(SeckillActivityVO activity) {
        try {
            // 活动详情先写 L2 Redis；若 Key 支持 L1，CacheClient 会同步回填 Caffeine。
            cacheClient.setWithLogicalExpire(RedisKeys.seckillActivity(activity.getId()), activity, ACTIVITY_TTL);
            cacheClient.delete(RedisKeys.seckillActivityNull(activity.getId()));
            // 库存和已购集合是强一致读写路径，只保存在 Redis，不进入本地缓存。
            redisExecutor.ensureStock(activity);
            return true;
        } catch (RuntimeException e) {
            log.warn("秒杀活动预热失败: activityId={}, error={}: {}",
                    activity.getId(), e.getClass().getSimpleName(), e.getMessage());
            return false;
        }
    }
}
