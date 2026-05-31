package com.shanpu.module.seckill.domain.msg;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Getter
@Setter
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class SeckillOrderMsg {

    private String orderId;
    private Long userId;
    private Long activityId;
    private Long productId;
    private Integer price;
    private Integer source;
    private LocalDateTime createdAt;
}
