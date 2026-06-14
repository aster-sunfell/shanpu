package com.shanpu.module.seckill.service;

import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;

import java.util.List;

public interface OrderDlqLogService {

    void record(String orderId, SeckillOrderMsg payload, String failReason);

    List<OrderDlqLog> listPending(int limit);

    void markRepublished(Long id);

    void incrementFailure(Long id, String failReason);

    void markError(Long id, String failReason, int failCount);
}
