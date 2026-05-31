package com.shanpu.config;

import com.shanpu.infra.kafka.KafkaTopics;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import lombok.RequiredArgsConstructor;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.common.TopicPartition;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.ContainerProperties;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.util.backoff.FixedBackOff;

@Configuration
@RequiredArgsConstructor
public class KafkaConfig {

    @Bean
    public NewTopic seckillOrderTopic() {
        return TopicBuilder.name(KafkaTopics.SECKILL_ORDER)
                .partitions(3)
                .replicas(1)
                .build();
    }

    @Bean
    public NewTopic seckillOrderDlqTopic() {
        return TopicBuilder.name(KafkaTopics.SECKILL_ORDER_DLQ)
                .partitions(1)
                .replicas(1)
                .build();
    }

    @Bean
    public DefaultErrorHandler seckillKafkaErrorHandler(
            KafkaTemplate<Object, Object> kafkaTemplate,
            OrderDlqLogService orderDlqLogService) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(
                kafkaTemplate,
                (record, ex) -> {
                    orderDlqLogService.record(String.valueOf(record.key()), ex.getMessage());
                    return new TopicPartition(KafkaTopics.SECKILL_ORDER_DLQ, 0);
                });
        return new DefaultErrorHandler(recoverer, new FixedBackOff(1000L, 3));
    }

    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, SeckillOrderMsg> manualAckListenerContainerFactory(
            ConsumerFactory<String, SeckillOrderMsg> consumerFactory,
            DefaultErrorHandler seckillKafkaErrorHandler) {
        ConcurrentKafkaListenerContainerFactory<String, SeckillOrderMsg> factory =
                new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(consumerFactory);
        factory.setConcurrency(3);
        factory.setCommonErrorHandler(seckillKafkaErrorHandler);
        factory.getContainerProperties().setAckMode(ContainerProperties.AckMode.MANUAL_IMMEDIATE);
        return factory;
    }
}
