package com.shanpu.module.job;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.infra.SeckillProducer;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class KafkaDlqRescueJobTest {

    private OrderDlqLogService orderDlqLogService;
    private SeckillProducer seckillProducer;
    private ObjectMapper objectMapper;
    private KafkaDlqRescueJob rescueJob;

    @BeforeEach
    void setUp() {
        orderDlqLogService = mock(OrderDlqLogService.class);
        seckillProducer = mock(SeckillProducer.class);
        objectMapper = new ObjectMapper();
        objectMapper.registerModule(new JavaTimeModule());
        objectMapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        rescueJob = new KafkaDlqRescueJob(orderDlqLogService, seckillProducer, objectMapper, 5, 10);
    }

    @Test
    void rescue_republishesMessageAndMarksRepublished() throws Exception {
        OrderDlqLog dlqLog = dlqLog(1L, "9000000000001", objectMapper.writeValueAsString(message()), 1);
        when(orderDlqLogService.listPending(10)).thenReturn(List.of(dlqLog));

        JobRunVO result = rescueJob.runOnce();

        assertEquals("dlq-rescue", result.getJob());
        assertEquals(1, result.getScanned());
        assertEquals(1, result.getSucceeded());
        assertEquals(0, result.getFailed());
        ArgumentCaptor<SeckillOrderMsg> msgCaptor = ArgumentCaptor.forClass(SeckillOrderMsg.class);
        verify(seckillProducer).send(msgCaptor.capture());
        assertEquals("9000000000001", msgCaptor.getValue().getOrderId());
        assertEquals(3001L, msgCaptor.getValue().getActivityId());
        verify(orderDlqLogService).markRepublished(1L);
        verify(orderDlqLogService, never()).incrementFailure(org.mockito.ArgumentMatchers.any(), anyString());
    }

    @Test
    void rescue_marksErrorWhenFailureThresholdReached() {
        OrderDlqLog dlqLog = dlqLog(1L, "9000000000001", "{}", 5);
        dlqLog.setFailReason("points not enough");
        when(orderDlqLogService.listPending(10)).thenReturn(List.of(dlqLog));

        JobRunVO result = rescueJob.runOnce();

        assertEquals(1, result.getScanned());
        assertEquals(0, result.getSucceeded());
        assertEquals(1, result.getFailed());
        verify(seckillProducer, never()).send(org.mockito.ArgumentMatchers.any());
        verify(orderDlqLogService).markError(1L, "points not enough", 5);
    }

    @Test
    void rescue_invalidPayloadIncrementsFailure() {
        OrderDlqLog dlqLog = dlqLog(1L, "9000000000001", "{invalid", 1);
        when(orderDlqLogService.listPending(10)).thenReturn(List.of(dlqLog));

        JobRunVO result = rescueJob.runOnce();

        assertEquals(1, result.getScanned());
        assertEquals(0, result.getSucceeded());
        assertEquals(1, result.getFailed());
        verify(seckillProducer, never()).send(org.mockito.ArgumentMatchers.any());
        verify(orderDlqLogService).incrementFailure(org.mockito.ArgumentMatchers.eq(1L), anyString());
    }

    @Test
    void rescue_invalidPayloadAtNextThresholdMarksError() {
        OrderDlqLog dlqLog = dlqLog(1L, "9000000000001", "{invalid", 4);
        when(orderDlqLogService.listPending(10)).thenReturn(List.of(dlqLog));

        JobRunVO result = rescueJob.runOnce();

        assertEquals(1, result.getFailed());
        verify(seckillProducer, never()).send(org.mockito.ArgumentMatchers.any());
        verify(orderDlqLogService).markError(org.mockito.ArgumentMatchers.eq(1L), anyString(), org.mockito.ArgumentMatchers.eq(5));
    }

    private SeckillOrderMsg message() {
        return SeckillOrderMsg.builder()
                .orderId("9000000000001")
                .userId(7L)
                .activityId(3001L)
                .productId(2L)
                .price(180)
                .source(4)
                .createdAt(LocalDateTime.of(2026, 6, 14, 12, 0))
                .build();
    }

    private OrderDlqLog dlqLog(Long id, String orderId, String payload, int failCount) {
        OrderDlqLog dlqLog = new OrderDlqLog();
        dlqLog.setId(id);
        dlqLog.setOrderId(orderId);
        dlqLog.setPayload(payload);
        dlqLog.setFailReason("failed");
        dlqLog.setFailCount(failCount);
        dlqLog.setStatus(OrderDlqLog.STATUS_PENDING);
        return dlqLog;
    }
}
