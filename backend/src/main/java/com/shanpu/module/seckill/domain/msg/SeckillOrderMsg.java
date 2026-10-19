package com.shanpu.module.seckill.domain.msg;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

/**
 * 超级秒杀异步下单消息体。
 *
 * <p>接口线程完成 Redis 预扣库存后，将本对象写入 Kafka 主 Topic；
 * 消费端再根据消息中的快照字段完成订单落库、扣积分和写积分流水。</p>
 */
@Getter
@Setter
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class SeckillOrderMsg {

    /**
     * 预生成订单号，也是 Kafka 消息 key 和消费端幂等判断依据。
     */
    private String orderId;

    /**
     * 抢购用户 ID。
     */
    private Long userId;

    /**
     * 秒杀活动 ID。
     */
    private Long activityId;

    /**
     * 活动关联商品 ID。
     */
    private Long productId;

    /**
     * 本次下单使用的秒杀积分价。
     */
    private Integer price;

    /**
     * 订单来源，超级秒杀固定为 {@code Order.SOURCE_SECKILL_SUPER}。
     */
    private Integer source;

    /**
     * 接口线程创建消息的时间。
     */
    private LocalDateTime createdAt;
}
