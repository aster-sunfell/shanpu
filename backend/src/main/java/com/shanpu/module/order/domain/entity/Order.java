package com.shanpu.module.order.domain.entity;

import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Getter
@Setter
@NoArgsConstructor
public class Order {

    public static final int SOURCE_NORMAL = 1;
    public static final int SOURCE_SECKILL_INCLUSIVE = 2;
    public static final int SOURCE_SECKILL_FLASH = 3;
    public static final int SOURCE_SECKILL_SUPER = 4;

    public static final int STATUS_PENDING_PAY = 1;
    public static final int STATUS_PAID = 2;
    public static final int STATUS_CANCELLED = 3;

    private Long id;
    private String orderId;
    private Long userId;
    private Long productId;
    private Long activityId;
    private Integer source;
    private Integer price;
    private Integer status;
    private LocalDateTime createdAt;
    private LocalDateTime paidAt;
    private LocalDateTime updatedAt;
}
