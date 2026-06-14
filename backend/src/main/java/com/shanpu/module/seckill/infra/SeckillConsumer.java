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

@Slf4j
@Component
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "shanpu.kafka.consumer", name = "enabled", havingValue = "true", matchIfMissing = true)
public class SeckillConsumer {

    private final SeckillOrderWriter orderWriter;

    @KafkaListener(
            topics = KafkaTopics.SECKILL_ORDER,
            groupId = "shanpu-seckill-consumer",
            containerFactory = "manualAckListenerContainerFactory"
    )
    public void consume(ConsumerRecord<String, SeckillOrderMsg> record, Acknowledgment ack) {
        SeckillOrderMsg msg = record.value();
        if (msg == null) {
            ack.acknowledge();
            return;
        }
        boolean created = orderWriter.createAsyncPaidOrder(msg);
        if (!created) {
            log.info("Duplicate seckill message skipped: orderId={}", msg.getOrderId());
        }
        ack.acknowledge();
    }
}
