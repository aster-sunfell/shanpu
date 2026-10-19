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

    /**
     * 定时执行秒杀库存对账。
     *
     * <p>默认每 5 分钟扫描一次，用数据库剩余库存收敛 Redis 库存异常。</p>
     */
    @Scheduled(cron = "${shanpu.seckill.stock-reconcile.cron:0 */5 * * * *}")
    public void reconcile() {
        runOnce();
    }

    /**
     * 执行一轮秒杀库存对账。
     *
     * <p>对账对象是近期仍可能被访问或补偿的活动，任务逐个比较数据库库存和 Redis 库存。</p>
     *
     * @return 本轮任务执行统计
     */
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
                log.warn("秒杀库存对账失败: activityId={}, error={}: {}",
                        activity.getId(), e.getClass().getSimpleName(), e.getMessage());
            }
        }
        return JobRunVO.of(JOB_NAME, activities.size(), succeeded, failed);
    }

    /**
     * 对账单个活动的 Redis 库存。
     *
     * <p>Redis 库存高于数据库剩余库存时存在超卖风险，任务会把 Redis 库存压回数据库值；
     * Redis 库存低于数据库时维持保守值，保留异步或补偿状态的处理空间。</p>
     *
     * @param activity 活动详情
     * @return true 表示本次对账实际修正了 Redis 库存
     */
    private boolean reconcileOne(SeckillActivityVO activity) {
        if (activity.getTier() == null || activity.getTier() == SeckillActivity.TIER_INCLUSIVE) {
            return false;
        }
        int dbRemaining = Math.max(0, activity.getTotalStock());
        int redisRemaining = redisExecutor.currentStock(activity);
        if (redisRemaining > dbRemaining) {
            // 向下修正 Redis 库存，收敛 Redis 比 DB 多放出库存的超卖风险。
            redisExecutor.setStock(activity.getId(), dbRemaining);
            log.error("秒杀库存对账已修正: activityId={}, redisStock={}, dbStock={}",
                    activity.getId(), redisRemaining, dbRemaining);
            return true;
        }
        return false;
    }
}
