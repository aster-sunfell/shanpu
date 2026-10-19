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

    /**
     * 创建订单超时关闭任务。
     *
     * @param orderMapper 订单 Mapper
     * @param redisExecutor 秒杀 Redis 执行器
     * @param cacheClient 缓存客户端
     * @param timeoutMinutes 待处理订单超时时间，单位分钟
     * @param batchSize 单轮最大处理订单数
     */
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

    /**
     * 定时关闭超时待处理订单。
     *
     * <p>超级秒杀异步链路异常留下的待处理订单，会在这里被取消并触发 Redis 回补。</p>
     */
    @Scheduled(cron = "${shanpu.order.timeout.cron:0 */1 * * * *}")
    public void closeTimeoutOrders() {
        runOnce();
    }

    /**
     * 执行一轮超时订单关闭。
     *
     * @return 本轮任务执行统计
     */
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
                log.warn("订单超时关闭失败: orderId={}, error={}: {}",
                        order.getOrderId(), e.getClass().getSimpleName(), e.getMessage());
            }
        }

        return JobRunVO.of(JOB_NAME, orders.size(), succeeded, failed);
    }

    /**
     * 取消单个超时待处理订单，并处理秒杀 Redis 回补与订单缓存失效。
     *
     * @param order 待取消订单
     * @return true 表示订单状态实际从待处理变为取消
     */
    private boolean cancelOne(Order order) {
        if (orderMapper.cancelPendingByOrderId(order.getOrderId()) == 0) {
            return false;
        }
        rollbackSeckillRedis(order);
        invalidateOrderCache(order);
        return true;
    }

    /**
     * 为限时或超级秒杀订单回补 Redis 预扣减状态。
     *
     * <p>普通订单和普惠档秒杀使用 MySQL 库存链路，Redis 回补仅覆盖限时和超级两档。</p>
     *
     * @param order 已取消订单
     */
    private void rollbackSeckillRedis(Order order) {
        if (order.getActivityId() == null || order.getSource() == null) {
            return;
        }
        if (order.getSource() == Order.SOURCE_SECKILL_FLASH
                || order.getSource() == Order.SOURCE_SECKILL_SUPER) {
            // 只有走过 Redis 预扣减的订单才需要撤销已购集合并回补库存。
            redisExecutor.rollback(order.getActivityId(), order.getUserId());
        }
    }

    /**
     * 删除超时订单的详情缓存。
     *
     * @param order 已取消订单
     */
    private void invalidateOrderCache(Order order) {
        try {
            cacheClient.delete(RedisKeys.orderDetail(order.getUserId(), order.getOrderId()));
        } catch (RuntimeException e) {
            log.warn("超时订单详情缓存失效失败: orderId={}, error={}: {}",
                    order.getOrderId(), e.getClass().getSimpleName(), e.getMessage());
        }
    }
}
