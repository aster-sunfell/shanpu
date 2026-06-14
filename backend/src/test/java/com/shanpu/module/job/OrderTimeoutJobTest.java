package com.shanpu.module.job;

import com.shanpu.cache.CacheClient;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OrderTimeoutJobTest {

    private OrderMapper orderMapper;
    private SeckillRedisExecutor redisExecutor;
    private CacheClient cacheClient;
    private OrderTimeoutJob timeoutJob;

    @BeforeEach
    void setUp() {
        orderMapper = mock(OrderMapper.class);
        redisExecutor = mock(SeckillRedisExecutor.class);
        cacheClient = mock(CacheClient.class);
        timeoutJob = new OrderTimeoutJob(orderMapper, redisExecutor, cacheClient, 15, 10);
    }

    @Test
    void runOnce_cancelsTimeoutOrderAndClearsCache() {
        Order order = order("9000000000001", Order.SOURCE_NORMAL, null);
        when(orderMapper.selectTimeoutPending(any(LocalDateTime.class), eq(10)))
                .thenReturn(List.of(order));
        when(orderMapper.cancelPendingByOrderId("9000000000001")).thenReturn(1);

        JobRunVO result = timeoutJob.runOnce();

        assertEquals("order-timeout", result.getJob());
        assertEquals(1, result.getScanned());
        assertEquals(1, result.getSucceeded());
        assertEquals(0, result.getFailed());
        verify(orderMapper).cancelPendingByOrderId("9000000000001");
        verify(cacheClient).delete(RedisKeys.orderDetail(7L, "9000000000001"));
        verify(redisExecutor, never()).rollback(any(), any());
    }

    @Test
    void runOnce_skipsWhenNoTimeoutOrder() {
        when(orderMapper.selectTimeoutPending(any(LocalDateTime.class), eq(10)))
                .thenReturn(List.of());

        JobRunVO result = timeoutJob.runOnce();

        assertEquals(0, result.getScanned());
        assertEquals(0, result.getSucceeded());
        assertEquals(0, result.getFailed());
        verify(orderMapper, never()).cancelPendingByOrderId(any());
    }

    @Test
    void runOnce_rollsBackRedisForFlashAndSuperOrders() {
        Order flash = order("9000000000002", Order.SOURCE_SECKILL_FLASH, 2001L);
        Order superOrder = order("9000000000003", Order.SOURCE_SECKILL_SUPER, 3001L);
        when(orderMapper.selectTimeoutPending(any(LocalDateTime.class), eq(10)))
                .thenReturn(List.of(flash, superOrder));
        when(orderMapper.cancelPendingByOrderId("9000000000002")).thenReturn(1);
        when(orderMapper.cancelPendingByOrderId("9000000000003")).thenReturn(1);

        JobRunVO result = timeoutJob.runOnce();

        assertEquals(2, result.getScanned());
        assertEquals(2, result.getSucceeded());
        assertEquals(0, result.getFailed());
        verify(redisExecutor).rollback(2001L, 7L);
        verify(redisExecutor).rollback(3001L, 7L);
    }

    @Test
    void runOnce_isIdempotentWhenOrderAlreadyCancelled() {
        Order order = order("9000000000001", Order.SOURCE_SECKILL_FLASH, 2001L);
        when(orderMapper.selectTimeoutPending(any(LocalDateTime.class), eq(10)))
                .thenReturn(List.of(order));
        when(orderMapper.cancelPendingByOrderId("9000000000001")).thenReturn(0);

        JobRunVO result = timeoutJob.runOnce();

        assertEquals(1, result.getScanned());
        assertEquals(0, result.getSucceeded());
        assertEquals(0, result.getFailed());
        verify(redisExecutor, never()).rollback(any(), any());
        verify(cacheClient, never()).delete(any());
    }

    @Test
    void runOnce_usesFifteenMinuteCutoff() {
        when(orderMapper.selectTimeoutPending(any(LocalDateTime.class), eq(10)))
                .thenReturn(List.of());

        timeoutJob.runOnce();

        ArgumentCaptor<LocalDateTime> cutoff = ArgumentCaptor.forClass(LocalDateTime.class);
        verify(orderMapper).selectTimeoutPending(cutoff.capture(), eq(10));
        long minutes = java.time.Duration.between(cutoff.getValue(), LocalDateTime.now()).toMinutes();
        assertEquals(15, minutes);
    }

    private Order order(String orderId, int source, Long activityId) {
        Order order = new Order();
        order.setOrderId(orderId);
        order.setUserId(7L);
        order.setProductId(2L);
        order.setActivityId(activityId);
        order.setSource(source);
        order.setPrice(180);
        order.setStatus(Order.STATUS_PENDING_PAY);
        order.setCreatedAt(LocalDateTime.now().minusMinutes(20));
        return order;
    }
}
