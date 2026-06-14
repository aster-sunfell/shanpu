package com.shanpu.module.job;

import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import com.shanpu.module.job.domain.vo.JobRunVO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.List;

@Slf4j
@Component
@RequiredArgsConstructor
public class StockReconcileJob {

    private static final String JOB_NAME = "stock-reconcile";

    private final SeckillActivityMapper activityMapper;
    private final SeckillRedisExecutor redisExecutor;

    @Scheduled(cron = "${shanpu.seckill.stock-reconcile.cron:0 */5 * * * *}")
    public void reconcile() {
        runOnce();
    }

    public JobRunVO runOnce() {
        LocalDateTime now = LocalDateTime.now();
        List<SeckillActivityVO> activities = activityMapper.selectReconcileList(now, now.minusHours(1));
        int succeeded = 0;
        int failed = 0;
        for (SeckillActivityVO activity : activities) {
            try {
                if (reconcileOne(activity)) {
                    succeeded++;
                }
            } catch (RuntimeException e) {
                failed++;
                log.warn("Seckill stock reconcile failed: activityId={}, error={}: {}",
                        activity.getId(), e.getClass().getSimpleName(), e.getMessage());
            }
        }
        return JobRunVO.of(JOB_NAME, activities.size(), succeeded, failed);
    }

    private boolean reconcileOne(SeckillActivityVO activity) {
        if (activity.getTier() == null || activity.getTier() == SeckillActivity.TIER_INCLUSIVE) {
            return false;
        }
        int dbRemaining = Math.max(0, activity.getTotalStock());
        int redisRemaining = redisExecutor.currentStock(activity);
        if (redisRemaining > dbRemaining) {
            redisExecutor.setStock(activity.getId(), dbRemaining);
            log.error("Reconciled seckill stock: activityId={}, redisStock={}, dbStock={}",
                    activity.getId(), redisRemaining, dbRemaining);
            return true;
        }
        return false;
    }
}
