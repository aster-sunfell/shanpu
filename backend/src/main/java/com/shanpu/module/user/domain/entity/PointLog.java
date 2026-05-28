package com.shanpu.module.user.domain.entity;

import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Getter
@Setter
@NoArgsConstructor
public class PointLog {

    public static final int BIZ_REGISTER_BONUS = 1;
    public static final int BIZ_ORDER_CONSUME = 2;
    public static final int BIZ_ORDER_REFUND = 3;

    private Long id;
    private Long userId;
    private Integer delta;
    private Integer balanceAfter;
    private Integer bizType;
    private String bizRef;
    private LocalDateTime createdAt;
}
