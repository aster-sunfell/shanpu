package com.shanpu.module.seckill.infra;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.kafka.KafkaTopics;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import lombok.RequiredArgsConstructor;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

/**
 * 超级秒杀异步下单生产者。
 *
 * <p>服务层完成 Redis 预扣减后调用本组件发送下单消息。发送失败会抛业务异常，
 * 由上层回滚 Redis 库存与已购集合。</p>
 */
@Component
@RequiredArgsConstructor
public class SeckillProducer {

    private final KafkaTemplate<String, SeckillOrderMsg> kafkaTemplate;

    /**
     * 发送超级秒杀异步下单消息。
     *
     * <p>方法等待 Kafka ack 后才返回，确保接口返回 PENDING 时消息已经进入主 Topic；
     * 如果发送失败，上层会回滚 Redis 预扣减。</p>
     *
     * @param msg 秒杀订单消息
     */
    public void send(SeckillOrderMsg msg) {
        try {
            // orderId 作为 key，可让同一订单的重投消息稳定进入同一分区。
            kafkaTemplate.send(KafkaTopics.SECKILL_ORDER, msg.getOrderId(), msg)
                    // 前台最多等待 3 秒 Kafka ack；拿到 ack 后才向用户返回 PENDING。
                    .get(3, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            // 恢复中断标记，交给上层按发送失败处理并回滚 Redis 预扣结果。
            Thread.currentThread().interrupt();
            throw new BusinessException(ResultCode.INTERNAL_ERROR, "秒杀请求提交失败，请稍后重试");
        } catch (Exception e) {
            throw new BusinessException(ResultCode.INTERNAL_ERROR, "秒杀请求提交失败，请稍后重试");
        }
    }
}
