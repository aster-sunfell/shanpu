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

@Slf4j
@Component
@RequiredArgsConstructor
public class SeckillWarmupJob {

    private static final String JOB_NAME = "seckill-warmup";
    private static final Duration ACTIVITY_TTL = Duration.ofMinutes(10);

    private final SeckillActivityMapper activityMapper;
    private final CacheClient cacheClient;
    private final SeckillRedisExecutor redisExecutor;

    @Scheduled(cron = "${shanpu.seckill.warmup.cron:0 */1 * * * *}")
    public void warmupUpcoming() {
        runOnce();
    }

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

    public void warmupOne(Long activityId) {
        SeckillActivityVO activity = activityMapper.selectDetailById(activityId);
        if (activity != null) {
            warmupOne(activity);
        }
    }

    private boolean warmupOne(SeckillActivityVO activity) {
        try {
            cacheClient.setWithLogicalExpire(RedisKeys.seckillActivity(activity.getId()), activity, ACTIVITY_TTL);
            redisExecutor.ensureStock(activity);
            return true;
        } catch (RuntimeException e) {
            log.warn("Seckill warmup failed: activityId={}, error={}: {}",
                    activity.getId(), e.getClass().getSimpleName(), e.getMessage());
            return false;
        }
    }
}
