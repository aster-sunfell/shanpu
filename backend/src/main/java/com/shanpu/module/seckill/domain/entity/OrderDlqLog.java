package com.shanpu.module.seckill.domain.entity;

import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

/**
 * 超级秒杀订单死信日志实体。
 *
 * <p>对应 {@code t_order_dlq_log}，保存消费失败的订单消息、失败原因、
 * 回投次数和当前处理状态。</p>
 */
@Getter
@Setter
@NoArgsConstructor
public class OrderDlqLog {

    /**
     * 待回投。
     */
    public static final int STATUS_PENDING = 0;

    /**
     * 已回投主 Topic。
     */
    public static final int STATUS_REPUBLISHED = 1;

    /**
     * 多次回投失败后的错误终态。
     */
    public static final int STATUS_ERROR = 2;

    private Long id;

    /**
     * 订单号，业务唯一键。
     */
    private String orderId;

    /**
     * 原始 {@link com.shanpu.module.seckill.domain.msg.SeckillOrderMsg} JSON。
     */
    private String payload;

    /**
     * 最近一次失败原因。
     */
    private String failReason;

    /**
     * 累计失败次数。
     */
    private Integer failCount;

    /**
     * 当前处理状态。
     */
    private Integer status;

    /**
     * 最近失败时间。
     */
    private LocalDateTime lastFailedAt;

    /**
     * 成功回投时间。
     */
    private LocalDateTime republishedAt;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}
