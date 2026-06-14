package com.shanpu.module.job;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.seckill.domain.entity.OrderDlqLog;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.infra.SeckillProducer;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

@Slf4j
@Component
@ConditionalOnProperty(prefix = "shanpu.kafka.dlq-rescue", name = "enabled", havingValue = "true", matchIfMissing = true)
public class KafkaDlqRescueJob {

    private static final String JOB_NAME = "dlq-rescue";

    private final OrderDlqLogService orderDlqLogService;
    private final SeckillProducer seckillProducer;
    private final ObjectMapper objectMapper;
    private final int maxFailCount;
    private final int batchSize;

    public KafkaDlqRescueJob(OrderDlqLogService orderDlqLogService,
                             SeckillProducer seckillProducer,
                             ObjectMapper objectMapper,
                             @Value("${shanpu.kafka.dlq-rescue.max-fail-count:5}") int maxFailCount,
                             @Value("${shanpu.kafka.dlq-rescue.batch-size:50}") int batchSize) {
        this.orderDlqLogService = orderDlqLogService;
        this.seckillProducer = seckillProducer;
        this.objectMapper = objectMapper;
        this.maxFailCount = Math.max(1, maxFailCount);
        this.batchSize = Math.max(1, batchSize);
    }

    @Scheduled(cron = "${shanpu.kafka.dlq-rescue.cron:0 */1 * * * *}")
    public void rescue() {
        runOnce();
    }

    public JobRunVO runOnce() {
        List<OrderDlqLog> logs = orderDlqLogService.listPending(batchSize);
        int succeeded = 0;
        int failed = 0;
        for (OrderDlqLog dlqLog : logs) {
            if (rescueOne(dlqLog)) {
                succeeded++;
            } else {
                failed++;
            }
        }
        return JobRunVO.of(JOB_NAME, logs.size(), succeeded, failed);
    }

    private boolean rescueOne(OrderDlqLog dlqLog) {
        int failCount = currentFailCount(dlqLog);
        if (failCount >= maxFailCount) {
            markError(dlqLog, dlqLog.getFailReason(), failCount);
            return false;
        }

        try {
            SeckillOrderMsg msg = objectMapper.readValue(dlqLog.getPayload(), SeckillOrderMsg.class);
            validatePayload(msg);
            seckillProducer.send(msg);
            orderDlqLogService.markRepublished(dlqLog.getId());
            log.info("Kafka DLQ message republished: orderId={}", dlqLog.getOrderId());
            return true;
        } catch (Exception e) {
            recordFailure(dlqLog, e);
            return false;
        }
    }

    private void validatePayload(SeckillOrderMsg msg) {
        if (msg == null
                || isBlank(msg.getOrderId())
                || msg.getUserId() == null
                || msg.getActivityId() == null
                || msg.getProductId() == null
                || msg.getPrice() == null
                || msg.getSource() == null) {
            throw new IllegalArgumentException("invalid SeckillOrderMsg payload");
        }
    }

    private void recordFailure(OrderDlqLog dlqLog, Exception e) {
        int nextFailCount = currentFailCount(dlqLog) + 1;
        String reason = reason(e);
        if (nextFailCount >= maxFailCount) {
            markError(dlqLog, reason, nextFailCount);
            return;
        }
        orderDlqLogService.incrementFailure(dlqLog.getId(), reason);
        log.warn("Kafka DLQ rescue failed: orderId={}, failCount={}, error={}: {}",
                dlqLog.getOrderId(), nextFailCount, e.getClass().getSimpleName(), e.getMessage());
    }

    private void markError(OrderDlqLog dlqLog, String reason, int failCount) {
        orderDlqLogService.markError(dlqLog.getId(), reason, failCount);
        log.error("Kafka DLQ rescue exceeded max fail count: orderId={}, failCount={}, reason={}",
                dlqLog.getOrderId(), failCount, reason);
    }

    private int currentFailCount(OrderDlqLog dlqLog) {
        return dlqLog.getFailCount() == null ? 0 : dlqLog.getFailCount();
    }

    private String reason(Exception e) {
        String message = e.getMessage();
        if (message == null || message.isBlank()) {
            return e.getClass().getSimpleName();
        }
        return message;
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
