package com.shanpu.module.seckill.infra;

import com.shanpu.infra.kafka.KafkaTopics;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.service.impl.SeckillOrderWriter;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

/**
 * 超级秒杀异步下单消费者。
 *
 * <p>本消费者从主 Topic 读取订单消息，交给 {@link SeckillOrderWriter}
 * 在本地事务中完成订单、库存、积分和流水写入。</p>
 */
@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "shanpu.kafka.consumer", name = "enabled", havingValue = "true", matchIfMissing = true)
public class SeckillConsumer {

    private final SeckillOrderWriter orderWriter;

    /**
     * 消费超级秒杀异步下单消息。
     *
     * <p>消费成功或识别为重复消息后手动 ack；业务异常会交给 Kafka 重试和 DLQ 链路处理。</p>
     *
     * @param record Kafka 消费记录
     * @param ack 手动提交句柄
     */
    @KafkaListener(
            topics = KafkaTopics.SECKILL_ORDER,
            groupId = "shanpu-seckill-consumer",
            containerFactory = "manualAckListenerContainerFactory"
    )
    public void consume(ConsumerRecord<String, SeckillOrderMsg> record, Acknowledgment ack) {
        SeckillOrderMsg msg = record.value();
        if (msg == null) {
            // 空消息没有业务含义，直接确认，避免同一条坏消息反复重试占用消费线程。
            ack.acknowledge();
            return;
        }
        boolean created = orderWriter.createAsyncPaidOrder(msg);
        if (!created) {
            // 订单已存在通常来自 Kafka 至少一次投递或 DLQ 回投，幂等跳过后仍可提交 offset。
            log.info("Duplicate seckill message skipped: orderId={}", msg.getOrderId());
        }
        // 只有业务处理完成或确认重复可跳过后才手动提交 offset。
        ack.acknowledge();
    }
}
