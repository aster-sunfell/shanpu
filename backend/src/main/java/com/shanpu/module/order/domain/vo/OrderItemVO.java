package com.shanpu.module.order.domain.vo;

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
public class OrderItemVO {

    private String orderId;
    private Long productId;
    private String productTitle;
    private String coverUrl;
    private Integer price;
    private String status;
    private Integer source;
    private LocalDateTime createdAt;
}
