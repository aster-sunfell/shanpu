package com.shanpu.module.seckill.infra;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.kafka.KafkaTopics;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import lombok.RequiredArgsConstructor;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

@Component
@RequiredArgsConstructor
public class SeckillProducer {

    private final KafkaTemplate<String, SeckillOrderMsg> kafkaTemplate;

    public void send(SeckillOrderMsg msg) {
        try {
            kafkaTemplate.send(KafkaTopics.SECKILL_ORDER, msg.getOrderId(), msg)
                    .get(3, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new BusinessException(ResultCode.INTERNAL_ERROR, "秒杀请求提交失败，请稍后重试");
        } catch (Exception e) {
            throw new BusinessException(ResultCode.INTERNAL_ERROR, "秒杀请求提交失败，请稍后重试");
        }
    }
}
