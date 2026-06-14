package com.shanpu.module.seckill.domain.entity;

import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Getter
@Setter
@NoArgsConstructor
public class OrderDlqLog {

    public static final int STATUS_PENDING = 0;
    public static final int STATUS_REPUBLISHED = 1;
    public static final int STATUS_ERROR = 2;

    private Long id;
    private String orderId;
    private String payload;
    private String failReason;
    private Integer failCount;
    private Integer status;
    private LocalDateTime lastFailedAt;
    private LocalDateTime republishedAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
