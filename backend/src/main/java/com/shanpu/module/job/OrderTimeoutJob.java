package com.shanpu.module.job;

import com.shanpu.cache.CacheClient;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

@Slf4j
@Component
public class OrderTimeoutJob {

    private static final String JOB_NAME = "order-timeout";
    private static final Duration DEFAULT_TIMEOUT = Duration.ofMinutes(15);

    private final OrderMapper orderMapper;
    private final SeckillRedisExecutor redisExecutor;
    private final CacheClient cacheClient;
    private final Duration timeout;
    private final int batchSize;

    public OrderTimeoutJob(OrderMapper orderMapper,
                           SeckillRedisExecutor redisExecutor,
                           CacheClient cacheClient,
                           @Value("${shanpu.order.timeout.minutes:15}") long timeoutMinutes,
                           @Value("${shanpu.order.timeout.batch-size:100}") int batchSize) {
        this.orderMapper = orderMapper;
        this.redisExecutor = redisExecutor;
        this.cacheClient = cacheClient;
        this.timeout = timeoutMinutes > 0 ? Duration.ofMinutes(timeoutMinutes) : DEFAULT_TIMEOUT;
        this.batchSize = Math.max(1, batchSize);
    }

    @Scheduled(cron = "${shanpu.order.timeout.cron:0 */1 * * * *}")
    public void closeTimeoutOrders() {
        runOnce();
    }

    public JobRunVO runOnce() {
        LocalDateTime timeoutBefore = LocalDateTime.now().minus(timeout);
        List<Order> orders = orderMapper.selectTimeoutPending(timeoutBefore, batchSize);
        int succeeded = 0;
        int failed = 0;

        for (Order order : orders) {
            try {
                if (cancelOne(order)) {
                    succeeded++;
                }
            } catch (RuntimeException e) {
                failed++;
                log.warn("Order timeout cancellation failed: orderId={}, error={}: {}",
                        order.getOrderId(), e.getClass().getSimpleName(), e.getMessage());
            }
        }

        return JobRunVO.of(JOB_NAME, orders.size(), succeeded, failed);
    }

    private boolean cancelOne(Order order) {
        if (orderMapper.cancelPendingByOrderId(order.getOrderId()) == 0) {
            return false;
        }
        rollbackSeckillRedis(order);
        invalidateOrderCache(order);
        return true;
    }

    private void rollbackSeckillRedis(Order order) {
        if (order.getActivityId() == null || order.getSource() == null) {
            return;
        }
        if (order.getSource() == Order.SOURCE_SECKILL_FLASH
                || order.getSource() == Order.SOURCE_SECKILL_SUPER) {
            redisExecutor.rollback(order.getActivityId(), order.getUserId());
        }
    }

    private void invalidateOrderCache(Order order) {
        try {
            cacheClient.delete(RedisKeys.orderDetail(order.getUserId(), order.getOrderId()));
        } catch (RuntimeException e) {
            log.warn("Order detail cache invalidation failed after timeout: orderId={}, error={}: {}",
                    order.getOrderId(), e.getClass().getSimpleName(), e.getMessage());
        }
    }
}
