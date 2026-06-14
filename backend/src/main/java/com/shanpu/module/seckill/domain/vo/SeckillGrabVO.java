package com.shanpu.module.seckill.domain.vo;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Getter
@Setter
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class SeckillGrabVO {

    private String orderId;
    private String status;
    private Integer tier;
}
