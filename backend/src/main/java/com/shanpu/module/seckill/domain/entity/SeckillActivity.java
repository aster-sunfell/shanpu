package com.shanpu.module.seckill.domain.entity;

import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Getter
@Setter
@NoArgsConstructor
public class SeckillActivity {

    public static final int TIER_INCLUSIVE = 1;
    public static final int TIER_FLASH = 2;
    public static final int TIER_SUPER = 3;

    public static final int STATUS_UPCOMING = 0;
    public static final int STATUS_ACTIVE = 1;
    public static final int STATUS_ENDED = 2;

    private Long id;
    private Long productId;
    private String title;
    private Integer tier;
    private Integer seckillPrice;
    private Integer totalStock;
    private LocalDateTime startAt;
    private LocalDateTime endAt;
    private Integer status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
