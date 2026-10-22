package com.shanpu.config;

import com.shanpu.infra.kafka.KafkaTopics;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.infra.OrderConsumptionPacer.ConsumptionInterruptedException;
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
import org.springframework.kafka.listener.CommonContainerStoppingErrorHandler;
import org.springframework.kafka.listener.CommonDelegatingErrorHandler;
import org.springframework.kafka.listener.CommonErrorHandler;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.util.backoff.FixedBackOff;

/**
 * Kafka 基础配置。
 *
 * <p>这里集中声明秒杀异步下单 Topic、死信 Topic、消费失败重试策略和手动 ack
 * 的监听容器工厂。超级秒杀链路依赖这些配置完成削峰落库与失败兜底。</p>
 */
@Configuration
@RequiredArgsConstructor
public class KafkaConfig {

    /**
     * 声明超级秒杀异步下单主 Topic。
     *
     * <p>主 Topic 使用 3 个分区，对齐消费端并发度，让不同订单号可以并行消费。</p>
     *
     * @return 主 Topic 定义
     */
    @Bean
    public NewTopic seckillOrderTopic() {
        return TopicBuilder.name(KafkaTopics.SECKILL_ORDER)
                .partitions(3)
                .replicas(1)
                .build();
    }

    /**
     * 声明超级秒杀异步下单死信 Topic。
     *
     * <p>死信 Topic 只承载重试耗尽后的失败消息，单分区即可满足本项目规模。</p>
     *
     * @return 死信 Topic 定义
     */
    @Bean
    public NewTopic seckillOrderDlqTopic() {
        return TopicBuilder.name(KafkaTopics.SECKILL_ORDER_DLQ)
                .partitions(1)
                .replicas(1)
                .build();
    }

    /**
     * 创建秒杀消费失败处理器。
     *
     * <p>消费异常先按固定间隔重试，仍失败时发送到死信 Topic，并同步记录
     * {@code t_order_dlq_log}，供定时回投任务继续补偿。</p>
     *
     * @param kafkaTemplate 死信转发使用的 KafkaTemplate
     * @param orderDlqLogService 死信日志服务
     * @return Kafka 默认错误处理器
     */
    @Bean
    public CommonErrorHandler seckillKafkaErrorHandler(
            KafkaTemplate<Object, Object> kafkaTemplate,
            OrderDlqLogService orderDlqLogService) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(
                kafkaTemplate,
                (record, ex) -> {
                    // 尽量保存完整业务 payload，后续 DLQ 回投才能重建原始订单消息。
                    SeckillOrderMsg payload = record.value() instanceof SeckillOrderMsg msg ? msg : null;
                    orderDlqLogService.record(String.valueOf(record.key()), payload, ex.getMessage());
                    // 所有失败消息集中投递到固定死信分区，便于定时任务顺序扫描处理。
                    return new TopicPartition(KafkaTopics.SECKILL_ORDER_DLQ, 0);
                });
        var handler = new CommonDelegatingErrorHandler(new DefaultErrorHandler(recoverer, new FixedBackOff(1000L, 3)));
        handler.setCauseChainTraversing(true);
        // An interrupted pacing wait must leave the message unacknowledged for redelivery.
        handler.addDelegate(ConsumptionInterruptedException.class, new CommonContainerStoppingErrorHandler());
        return handler;
    }

    /**
     * 创建手动 ack 的秒杀订单监听容器工厂。
     *
     * <p>消费端只有在订单实际落库成功或确认重复消息可跳过后才提交 offset，
     * 避免业务未完成但 Kafka offset 已提交。</p>
     *
     * @param consumerFactory 消费者工厂
     * @param seckillKafkaErrorHandler 秒杀消费失败处理器
     * @return 监听容器工厂
     */
    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, SeckillOrderMsg> manualAckListenerContainerFactory(
            ConsumerFactory<String, SeckillOrderMsg> consumerFactory,
            CommonErrorHandler seckillKafkaErrorHandler) {
        ConcurrentKafkaListenerContainerFactory<String, SeckillOrderMsg> factory =
                new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(consumerFactory);
        // 并发度与主 Topic 分区数保持一致，单实例内最多 3 个消费线程并行落库。
        factory.setConcurrency(3);
        factory.setCommonErrorHandler(seckillKafkaErrorHandler);
        // 手动立即提交 offset，单条消息成功后马上确认，降低重复消费窗口。
        factory.getContainerProperties().setAckMode(ContainerProperties.AckMode.MANUAL_IMMEDIATE);
        return factory;
    }
}
