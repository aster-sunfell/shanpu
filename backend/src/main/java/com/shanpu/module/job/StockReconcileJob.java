package com.shanpu.module.job;

import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import com.shanpu.module.order.mapper.OrderMapper;
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

    private final SeckillActivityMapper activityMapper;
    private final OrderMapper orderMapper;
    private final SeckillRedisExecutor redisExecutor;

    @Scheduled(cron = "0 */5 * * * *")
    public void reconcile() {
        LocalDateTime now = LocalDateTime.now();
        List<SeckillActivityVO> activities = activityMapper.selectReconcileList(now, now.minusHours(1));
        for (SeckillActivityVO activity : activities) {
            if (activity.getTier() == null || activity.getTier() == SeckillActivity.TIER_INCLUSIVE) {
                continue;
            }
            int dbRemaining = Math.max(0, activity.getTotalStock());
            int redisRemaining = redisExecutor.currentStock(activity);
            if (redisRemaining > dbRemaining) {
                redisExecutor.setStock(activity.getId(), dbRemaining);
                log.error("Reconciled seckill stock: activityId={}, redisStock={}, dbStock={}",
                        activity.getId(), redisRemaining, dbRemaining);
            }
        }
    }
}
