package com.shanpu.module.product.domain.entity;

import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

@Getter
@Setter
@NoArgsConstructor
public class Product {

    public static final int STATUS_ON = 1;
    public static final int STATUS_OFF = 0;

    private Long id;
    private String title;
    private String coverUrl;
    private String description;
    private Integer price;
    private Integer stock;
    private Integer status;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
