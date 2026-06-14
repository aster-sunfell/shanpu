package com.shanpu.module.seckill.service.impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.mapper.OrderDlqLogMapper;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
@RequiredArgsConstructor
public class OrderDlqLogServiceImpl implements OrderDlqLogService {

    private final OrderDlqLogMapper orderDlqLogMapper;
    private final ObjectMapper objectMapper;

    @Override
    public void record(String orderId, SeckillOrderMsg payload, String failReason) {
        String key = normalizeOrderId(orderId, payload);
        orderDlqLogMapper.upsert(key, serializePayload(payload), normalizeReason(failReason));
    }

    @Override
    public List<OrderDlqLog> listPending(int limit) {
        return orderDlqLogMapper.selectPending(Math.max(1, limit));
    }

    @Override
    public void markRepublished(Long id) {
        orderDlqLogMapper.markRepublished(id);
    }

    @Override
    public void incrementFailure(Long id, String failReason) {
        orderDlqLogMapper.incrementFailure(id, normalizeReason(failReason));
    }

    @Override
    public void markError(Long id, String failReason, int failCount) {
        orderDlqLogMapper.markError(id, normalizeReason(failReason), Math.max(1, failCount));
    }

    private String normalizeOrderId(String orderId, SeckillOrderMsg payload) {
        if (payload != null && payload.getOrderId() != null && !payload.getOrderId().isBlank()) {
            return payload.getOrderId();
        }
        return orderId == null || orderId.isBlank() ? "unknown" : orderId;
    }

    private String normalizeReason(String failReason) {
        String reason = failReason == null || failReason.isBlank() ? "unknown" : failReason;
        if (reason.length() > 512) {
            reason = reason.substring(0, 512);
        }
        return reason;
    }

    private String serializePayload(SeckillOrderMsg payload) {
        if (payload == null) {
            return "{}";
        }
        try {
            return objectMapper.writeValueAsString(payload);
        } catch (JsonProcessingException e) {
            return "{}";
        }
    }
}
