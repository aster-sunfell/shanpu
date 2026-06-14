package com.shanpu.infra.kafka;

public final class KafkaTopics {

    public static final String SECKILL_ORDER = "shanpu.seckill.order";
    public static final String SECKILL_ORDER_DLQ = "shanpu.seckill.order.dlq";

    private KafkaTopics() {
    }
}
