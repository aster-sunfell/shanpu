package com.shanpu.module.seckill.domain.vo;

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
public class SeckillActivityVO {

    private Long id;
    private Long productId;
    private String productTitle;
    private String coverUrl;
    private String title;
    private Integer tier;
    private Integer originalPrice;
    private Integer seckillPrice;
    private Integer totalStock;
    private Integer stock;
    private String status;
    private Integer statusCode;
    private LocalDateTime startAt;
    private LocalDateTime endAt;
}
