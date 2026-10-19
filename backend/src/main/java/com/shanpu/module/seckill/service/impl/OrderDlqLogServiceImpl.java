package com.shanpu.module.seckill.service.impl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shanpu.module.job.domain.vo.DlqStatsVO;
import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.mapper.OrderDlqLogMapper;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 超级秒杀订单死信日志服务实现。
 *
 * <p>该实现负责把 Kafka 错误处理器传入的订单消息序列化为 JSON，
 * 并维护 DLQ 回投过程中的状态和失败次数。</p>
 */
@Service
@RequiredArgsConstructor
public class OrderDlqLogServiceImpl implements OrderDlqLogService {

    private final OrderDlqLogMapper orderDlqLogMapper;
    private final ObjectMapper objectMapper;

    @Override
    public void record(String orderId, SeckillOrderMsg payload, String failReason) {
        // 优先使用 payload 内的订单号，保证 Kafka key 缺失时仍能按业务订单去重。
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

    @Override
    public DlqStatsVO stats() {
        // 管理接口直接使用三种状态计数，便于观察 DLQ 是否堆积。
        return DlqStatsVO.builder()
                .pending(orderDlqLogMapper.countByStatus(OrderDlqLog.STATUS_PENDING))
                .republished(orderDlqLogMapper.countByStatus(OrderDlqLog.STATUS_REPUBLISHED))
                .error(orderDlqLogMapper.countByStatus(OrderDlqLog.STATUS_ERROR))
                .build();
    }

    /**
     * 归一化订单号。
     *
     * @param orderId Kafka 记录 key 或外部传入订单号
     * @param payload 秒杀订单消息
     * @return 可用于唯一索引的订单号
     */
    private String normalizeOrderId(String orderId, SeckillOrderMsg payload) {
        if (payload != null && payload.getOrderId() != null && !payload.getOrderId().isBlank()) {
            return payload.getOrderId();
        }
        return orderId == null || orderId.isBlank() ? "unknown" : orderId;
    }

    /**
     * 归一化失败原因。
     *
     * @param failReason 原始失败原因
     * @return 长度受控的失败原因
     */
    private String normalizeReason(String failReason) {
        String reason = failReason == null || failReason.isBlank() ? "unknown" : failReason;
        if (reason.length() > 512) {
            reason = reason.substring(0, 512);
        }
        return reason;
    }

    /**
     * 将订单消息序列化为 JSON。
     *
     * @param payload 秒杀订单消息
     * @return JSON 字符串，无法序列化时返回空对象
     */
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
