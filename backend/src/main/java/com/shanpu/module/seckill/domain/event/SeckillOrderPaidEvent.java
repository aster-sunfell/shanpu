package com.shanpu.module.seckill.domain.event;

/** Published within the order transaction; consumers choose the transaction phase they need. */
public record SeckillOrderPaidEvent(Long activityId, Long userId, String orderId) {
}
