package com.shanpu.module.job;

import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.user.domain.entity.PointLog;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OrderReconcileJobTest {

    private OrderMapper orderMapper;
    private PointLogMapper pointLogMapper;
    private UserMapper userMapper;
    private OrderReconcileJob reconcileJob;

    @BeforeEach
    void setUp() {
        orderMapper = mock(OrderMapper.class);
        pointLogMapper = mock(PointLogMapper.class);
        userMapper = mock(UserMapper.class);
        reconcileJob = new OrderReconcileJob(orderMapper, pointLogMapper, userMapper, 10);
    }

    @Test
    void runOnce_backfillsMissingConsumeLog() {
        Order order = paidOrder("9000000000001");
        when(orderMapper.selectPaidOrdersMissingPointLog(10)).thenReturn(List.of(order));
        when(pointLogMapper.countByBizTypeAndRef(PointLog.BIZ_ORDER_CONSUME, "9000000000001")).thenReturn(0);
        when(userMapper.selectPointsById(7L)).thenReturn(9820);

        JobRunVO result = reconcileJob.runOnce();

        assertEquals("order-reconcile", result.getJob());
        assertEquals(1, result.getScanned());
        assertEquals(1, result.getSucceeded());
        assertEquals(0, result.getFailed());

        ArgumentCaptor<PointLog> logCaptor = ArgumentCaptor.forClass(PointLog.class);
        verify(pointLogMapper).insert(logCaptor.capture());
        PointLog pointLog = logCaptor.getValue();
        assertEquals(7L, pointLog.getUserId());
        assertEquals(-180, pointLog.getDelta());
        assertEquals(9820, pointLog.getBalanceAfter());
        assertEquals(PointLog.BIZ_ORDER_CONSUME, pointLog.getBizType());
        assertEquals("9000000000001", pointLog.getBizRef());
    }

    @Test
    void runOnce_skipsOrderWhenConsumeLogAlreadyExists() {
        Order order = paidOrder("9000000000001");
        when(orderMapper.selectPaidOrdersMissingPointLog(10)).thenReturn(List.of(order));
        when(pointLogMapper.countByBizTypeAndRef(PointLog.BIZ_ORDER_CONSUME, "9000000000001")).thenReturn(1);

        JobRunVO result = reconcileJob.runOnce();

        assertEquals(1, result.getScanned());
        assertEquals(0, result.getSucceeded());
        assertEquals(0, result.getFailed());
        verify(pointLogMapper, never()).insert(any());
        verify(userMapper, never()).selectPointsById(any());
    }

    private Order paidOrder(String orderId) {
        Order order = new Order();
        order.setOrderId(orderId);
        order.setUserId(7L);
        order.setProductId(2L);
        order.setActivityId(3001L);
        order.setSource(Order.SOURCE_SECKILL_SUPER);
        order.setPrice(180);
        order.setStatus(Order.STATUS_PAID);
        return order;
    }
}
