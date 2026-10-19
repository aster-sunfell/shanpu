package com.shanpu.infra.kafka;

/**
 * Kafka Topic 常量。
 *
 * <p>秒杀异步下单链路统一使用这里的 Topic 名称，避免生产者、消费者、
 * 死信回投任务之间出现字符串不一致。</p>
 */
public final class KafkaTopics {

    /**
     * 超级秒杀异步下单主 Topic。
     */
    public static final String SECKILL_ORDER = "shanpu.seckill.order";

    /**
     * 超级秒杀下单消费失败后的死信 Topic。
     */
    public static final String SECKILL_ORDER_DLQ = "shanpu.seckill.order.dlq";

    private KafkaTopics() {
    }
}
