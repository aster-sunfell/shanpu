package com.shanpu.module.seckill.service.impl;

import com.shanpu.module.seckill.mapper.OrderDlqLogMapper;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class OrderDlqLogServiceImpl implements OrderDlqLogService {

    private final OrderDlqLogMapper orderDlqLogMapper;

    @Override
    public void record(String orderId, String failReason) {
        String key = orderId == null || orderId.isBlank() ? "unknown" : orderId;
        String reason = failReason == null || failReason.isBlank() ? "unknown" : failReason;
        if (reason.length() > 512) {
            reason = reason.substring(0, 512);
        }
        orderDlqLogMapper.upsert(key, reason);
    }
}
