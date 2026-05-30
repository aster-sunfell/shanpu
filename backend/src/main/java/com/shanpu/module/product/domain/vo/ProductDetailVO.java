package com.shanpu.module.product.domain.vo;

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
public class ProductDetailVO {

    private Long id;
    private String title;
    private String coverUrl;
    private String description;
    private Integer price;
    private Integer stock;
    private Integer status;
}
