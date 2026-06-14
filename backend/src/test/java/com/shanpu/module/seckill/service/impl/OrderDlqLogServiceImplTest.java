package com.shanpu.module.seckill.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.mapper.OrderDlqLogMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class OrderDlqLogServiceImplTest {

    private OrderDlqLogMapper orderDlqLogMapper;
    private OrderDlqLogServiceImpl orderDlqLogService;

    @BeforeEach
    void setUp() {
        orderDlqLogMapper = mock(OrderDlqLogMapper.class);
        ObjectMapper objectMapper = new ObjectMapper();
        objectMapper.registerModule(new JavaTimeModule());
        objectMapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        orderDlqLogService = new OrderDlqLogServiceImpl(orderDlqLogMapper, objectMapper);
    }

    @Test
    void record_serializesFullPayloadAndTruncatesReason() {
        SeckillOrderMsg msg = SeckillOrderMsg.builder()
                .orderId("9000000000001")
                .userId(7L)
                .activityId(3001L)
                .productId(2L)
                .price(180)
                .source(4)
                .createdAt(LocalDateTime.of(2026, 6, 14, 12, 0))
                .build();
        String longReason = "x".repeat(600);

        orderDlqLogService.record("fallback", msg, longReason);

        ArgumentCaptor<String> payloadCaptor = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> reasonCaptor = ArgumentCaptor.forClass(String.class);
        verify(orderDlqLogMapper).upsert(eq("9000000000001"), payloadCaptor.capture(), reasonCaptor.capture());
        assertTrue(payloadCaptor.getValue().contains("\"orderId\":\"9000000000001\""));
        assertTrue(payloadCaptor.getValue().contains("\"activityId\":3001"));
        assertTrue(payloadCaptor.getValue().contains("\"source\":4"));
        assertEquals(512, reasonCaptor.getValue().length());
    }
}
