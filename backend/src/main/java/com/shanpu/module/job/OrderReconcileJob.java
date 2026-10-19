package com.shanpu.module.job;

import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.user.domain.entity.PointLog;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

@Slf4j
@Component
public class OrderReconcileJob {

    private static final String JOB_NAME = "order-reconcile";

    private final OrderMapper orderMapper;
    private final PointLogMapper pointLogMapper;
    private final UserMapper userMapper;
    private final int batchSize;

    public OrderReconcileJob(OrderMapper orderMapper,
                             PointLogMapper pointLogMapper,
                             UserMapper userMapper,
                             @Value("${shanpu.order.reconcile.batch-size:100}") int batchSize) {
        this.orderMapper = orderMapper;
        this.pointLogMapper = pointLogMapper;
        this.userMapper = userMapper;
        this.batchSize = Math.max(1, batchSize);
    }

    @Scheduled(cron = "${shanpu.order.reconcile.cron:0 */5 * * * *}")
    public void reconcile() {
        runOnce();
    }

    public JobRunVO runOnce() {
        List<Order> orders = orderMapper.selectPaidOrdersMissingPointLog(batchSize);
        int succeeded = 0;
        int failed = 0;

        for (Order order : orders) {
            try {
                if (reconcileOne(order)) {
                    succeeded++;
                }
            } catch (RuntimeException e) {
                failed++;
                log.warn("订单积分流水对账失败: orderId={}, error={}: {}",
                        order.getOrderId(), e.getClass().getSimpleName(), e.getMessage());
            }
        }

        return JobRunVO.of(JOB_NAME, orders.size(), succeeded, failed);
    }

    private boolean reconcileOne(Order order) {
        if (pointLogMapper.countByBizTypeAndRef(PointLog.BIZ_ORDER_CONSUME, order.getOrderId()) > 0) {
            return false;
        }
        Integer balanceAfter = userMapper.selectPointsById(order.getUserId());
        if (balanceAfter == null) {
            throw new IllegalStateException("user points not found");
        }

        PointLog pointLog = new PointLog();
        pointLog.setUserId(order.getUserId());
        pointLog.setDelta(-order.getPrice());
        pointLog.setBalanceAfter(balanceAfter);
        pointLog.setBizType(PointLog.BIZ_ORDER_CONSUME);
        pointLog.setBizRef(order.getOrderId());
        pointLogMapper.insert(pointLog);
        log.error("订单缺失积分流水已补齐: orderId={}, userId={}",
                order.getOrderId(), order.getUserId());
        return true;
    }
}
