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

/**
 * Kafka 死信订单回投任务。
 *
 * <p>消费端重试耗尽后会把超级秒杀订单消息写入 DLQ，并同步记录到
 * {@code t_order_dlq_log}。本任务定时扫描待处理记录，校验 payload 后重新发送
 * 到主 Topic，让消费端再次尝试异步落库。</p>
 */
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
        // 配置值最小为 1，避免错误配置导致任务永远不进入终态或批量数为 0。
        this.maxFailCount = Math.max(1, maxFailCount);
        this.batchSize = Math.max(1, batchSize);
    }

    /**
     * 定时触发一次 DLQ 回投。
     */
    @Scheduled(cron = "${shanpu.kafka.dlq-rescue.cron:0 */1 * * * *}")
    public void rescue() {
        runOnce();
    }

    /**
     * 执行一次 DLQ 回投扫描。
     *
     * <p>手动触发接口与定时调度复用该方法，便于运维在修复积分或库存等问题后
     * 主动推进死信消息重新消费。</p>
     *
     * @return 本轮任务执行统计
     */
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

    /**
     * 回投单条 DLQ 记录。
     *
     * @param dlqLog 死信日志记录
     * @return true 表示回投成功，false 表示本条仍未恢复
     */
    private boolean rescueOne(OrderDlqLog dlqLog) {
        int failCount = currentFailCount(dlqLog);
        if (failCount >= maxFailCount) {
            // 失败次数已达上限，直接进入 ERROR 终态，避免无限回投同一条坏消息。
            markError(dlqLog, dlqLog.getFailReason(), failCount);
            return false;
        }

        try {
            // payload 是进入 DLQ 时保存的原始 SeckillOrderMsg JSON。
            SeckillOrderMsg msg = objectMapper.readValue(dlqLog.getPayload(), SeckillOrderMsg.class);
            validatePayload(msg);
            // 回投仍走生产者的同步 ack 逻辑，确保消息进入主 Topic 后再标记已回投。
            seckillProducer.send(msg);
            orderDlqLogService.markRepublished(dlqLog.getId());
            log.info("Kafka 死信队列消息已回投: orderId={}", dlqLog.getOrderId());
            return true;
        } catch (Exception e) {
            recordFailure(dlqLog, e);
            return false;
        }
    }

    /**
     * 校验回投所需的最小消息字段。
     *
     * @param msg 待回投的秒杀订单消息
     */
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

    /**
     * 记录一次回投失败。
     *
     * @param dlqLog 死信日志记录
     * @param e 回投异常
     */
    private void recordFailure(OrderDlqLog dlqLog, Exception e) {
        int nextFailCount = currentFailCount(dlqLog) + 1;
        String reason = reason(e);
        if (nextFailCount >= maxFailCount) {
            // 本次失败后达到上限，写 ERROR 状态并输出错误日志。
            markError(dlqLog, reason, nextFailCount);
            return;
        }
        // 仍未达到上限时保留 PENDING 状态，等待下一轮定时任务继续回投。
        orderDlqLogService.incrementFailure(dlqLog.getId(), reason);
        log.warn("Kafka 死信队列回投失败: orderId={}, failCount={}, error={}: {}",
                dlqLog.getOrderId(), nextFailCount, e.getClass().getSimpleName(), e.getMessage());
    }

    /**
     * 将死信记录标记为错误终态。
     *
     * @param dlqLog 死信日志记录
     * @param reason 失败原因
     * @param failCount 失败次数
     */
    private void markError(OrderDlqLog dlqLog, String reason, int failCount) {
        orderDlqLogService.markError(dlqLog.getId(), reason, failCount);
        log.error("Kafka 死信队列回投超过最大失败次数: orderId={}, failCount={}, reason={}",
                dlqLog.getOrderId(), failCount, reason);
    }

    /**
     * 读取失败次数，数据库空值按 0 处理。
     *
     * @param dlqLog 死信日志记录
     * @return 当前失败次数
     */
    private int currentFailCount(OrderDlqLog dlqLog) {
        return dlqLog.getFailCount() == null ? 0 : dlqLog.getFailCount();
    }

    /**
     * 提取异常原因文本。
     *
     * @param e 异常
     * @return 可写入数据库的失败原因
     */
    private String reason(Exception e) {
        String message = e.getMessage();
        if (message == null || message.isBlank()) {
            return e.getClass().getSimpleName();
        }
        return message;
    }

    /**
     * 判断字符串是否为空。
     *
     * @param value 字符串
     * @return true 表示为空
     */
    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
