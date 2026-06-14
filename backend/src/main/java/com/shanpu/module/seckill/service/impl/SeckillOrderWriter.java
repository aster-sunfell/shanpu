package com.shanpu.module.seckill.service.impl;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import com.shanpu.module.user.domain.entity.PointLog;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class SeckillOrderWriter {

    private final SeckillActivityMapper activityMapper;
    private final OrderMapper orderMapper;
    private final UserMapper userMapper;
    private final PointLogMapper pointLogMapper;
    private final TransactionTemplate transactionTemplate;

    public void createSyncPaidOrder(Long userId, String orderId, Long activityId,
                                    Long productId, int price, int source) {
        transactionTemplate.executeWithoutResult(status -> {
            if (activityMapper.deductStock(activityId) == 0) {
                throw new BusinessException(ResultCode.STOCK_SHORTAGE);
            }
            if (userMapper.deductPoints(userId, price) == 0) {
                throw new BusinessException(ResultCode.POINTS_NOT_ENOUGH);
            }
            insertOrder(userId, orderId, activityId, productId, price, source);
            insertPointLog(userId, orderId, price);
        });
    }

    public boolean createAsyncPaidOrder(SeckillOrderMsg msg) {
        Boolean created = transactionTemplate.execute(status -> {
            if (orderMapper.existsByOrderId(msg.getOrderId()) > 0) {
                return false;
            }
            int inserted = insertOrderIgnore(msg);
            if (inserted == 0) {
                if (orderMapper.existsByOrderId(msg.getOrderId()) > 0) {
                    return false;
                }
                throw new BusinessException(ResultCode.DUPLICATE_ORDER);
            }
            if (activityMapper.deductStock(msg.getActivityId()) == 0) {
                throw new BusinessException(ResultCode.STOCK_SHORTAGE);
            }
            if (userMapper.deductPoints(msg.getUserId(), msg.getPrice()) == 0) {
                throw new BusinessException(ResultCode.POINTS_NOT_ENOUGH);
            }
            insertPointLog(msg.getUserId(), msg.getOrderId(), msg.getPrice());
            return true;
        });
        return Boolean.TRUE.equals(created);
    }

    private void insertOrder(Long userId, String orderId, Long activityId,
                             Long productId, int price, int source) {
        try {
            Order order = new Order();
            order.setOrderId(orderId);
            order.setUserId(userId);
            order.setProductId(productId);
            order.setActivityId(activityId);
            order.setSource(source);
            order.setPrice(price);
            order.setStatus(Order.STATUS_PAID);
            order.setPaidAt(LocalDateTime.now());
            orderMapper.insert(order);
        } catch (DuplicateKeyException e) {
            throw new BusinessException(ResultCode.DUPLICATE_ORDER);
        }
    }

    private int insertOrderIgnore(SeckillOrderMsg msg) {
        Order order = new Order();
        order.setOrderId(msg.getOrderId());
        order.setUserId(msg.getUserId());
        order.setProductId(msg.getProductId());
        order.setActivityId(msg.getActivityId());
        order.setSource(msg.getSource());
        order.setPrice(msg.getPrice());
        order.setStatus(Order.STATUS_PAID);
        order.setPaidAt(LocalDateTime.now());
        return orderMapper.insertIgnore(order);
    }

    private void insertPointLog(Long userId, String orderId, int price) {
        Integer balanceAfter = userMapper.selectPointsById(userId);
        PointLog pointLog = new PointLog();
        pointLog.setUserId(userId);
        pointLog.setDelta(-price);
        pointLog.setBalanceAfter(balanceAfter);
        pointLog.setBizType(PointLog.BIZ_ORDER_CONSUME);
        pointLog.setBizRef(orderId);
        pointLogMapper.insert(pointLog);
    }
}
