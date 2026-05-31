package com.shanpu.module.order.service.impl;

import cn.hutool.core.lang.Snowflake;
import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.PageResult;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.domain.vo.OrderDetailVO;
import com.shanpu.module.order.domain.vo.OrderItemVO;
import com.shanpu.module.order.domain.vo.OrderVO;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.order.service.OrderService;
import com.shanpu.module.product.domain.entity.Product;
import com.shanpu.module.product.mapper.ProductMapper;
import com.shanpu.module.user.domain.entity.PointLog;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class OrderServiceImpl implements OrderService {

    private final ProductMapper productMapper;
    private final UserMapper userMapper;
    private final OrderMapper orderMapper;
    private final PointLogMapper pointLogMapper;
    private final Snowflake snowflake;
    private final CacheClient cacheClient;

    private static final int DEFAULT_SIZE = 20;
    private static final int MAX_SIZE = 50;
    private static final Duration ORDER_DETAIL_TTL = Duration.ofSeconds(30);
    private static final Duration ORDER_NULL_TTL = Duration.ofSeconds(30);

    @Override
    @Transactional(rollbackFor = Exception.class)
    public OrderVO createNormalOrder(Long userId, Long productId, int quantity) {
        Product product = productMapper.selectById(productId);
        if (product == null || product.getStatus() == null || product.getStatus() != Product.STATUS_ON) {
            throw new BusinessException(ResultCode.NOT_FOUND);
        }

        // 行锁防超卖：条件 UPDATE 受影响行数为 0 即库存不足
        if (productMapper.deductStock(productId, quantity) == 0) {
            throw new BusinessException(ResultCode.STOCK_SHORTAGE);
        }

        // 行锁扣积分：条件 UPDATE 受影响行数为 0 即积分不足，事务回滚已扣库存
        int cost = product.getPrice() * quantity;
        if (userMapper.deductPoints(userId, cost) == 0) {
            throw new BusinessException(ResultCode.POINTS_NOT_ENOUGH);
        }

        String orderId = snowflake.nextIdStr();
        Order order = new Order();
        order.setOrderId(orderId);
        order.setUserId(userId);
        order.setProductId(productId);
        order.setActivityId(null);
        order.setSource(Order.SOURCE_NORMAL);
        order.setPrice(cost);
        order.setStatus(Order.STATUS_PAID);
        order.setPaidAt(LocalDateTime.now());
        orderMapper.insert(order);

        Integer balanceAfter = userMapper.selectPointsById(userId);
        PointLog pointLog = new PointLog();
        pointLog.setUserId(userId);
        pointLog.setDelta(-cost);
        pointLog.setBalanceAfter(balanceAfter);
        pointLog.setBizType(PointLog.BIZ_ORDER_CONSUME);
        pointLog.setBizRef(orderId);
        pointLogMapper.insert(pointLog);

        invalidateProductCacheAfterCommit(productId);

        return OrderVO.builder().orderId(orderId).status("PAID").build();
    }

    @Override
    public PageResult<OrderItemVO> getMyOrders(Long userId, String status, int page, int size) {
        Integer statusCode = toStatusCode(status);
        int p = Math.max(page, 1);
        int s = size <= 0 ? DEFAULT_SIZE : Math.min(size, MAX_SIZE);
        int offset = (p - 1) * s;
        List<OrderItemVO> list = orderMapper.selectPageByUser(userId, statusCode, offset, s);
        long total = orderMapper.countByUser(userId, statusCode);
        return PageResult.<OrderItemVO>builder().list(list).total(total).build();
    }

    @Override
    public OrderDetailVO getOrderDetail(Long userId, String orderId) {
        OrderDetailVO vo = cacheClient.queryWithMutex(
                RedisKeys.orderDetail(userId, orderId),
                cacheClient.javaType(OrderDetailVO.class),
                () -> orderMapper.selectByOrderId(userId, orderId),
                RedisKeys.orderDetailLock(userId, orderId),
                ORDER_DETAIL_TTL,
                ORDER_NULL_TTL
        );
        if (vo == null) {
            throw new BusinessException(ResultCode.NOT_FOUND);
        }
        return vo;
    }

    private Integer toStatusCode(String status) {
        if (status == null || status.isBlank()) {
            return null;
        }
        return switch (status) {
            case "PENDING" -> Order.STATUS_PENDING_PAY;
            case "PAID" -> Order.STATUS_PAID;
            case "CANCELLED" -> Order.STATUS_CANCELLED;
            default -> throw new BusinessException(ResultCode.PARAM_INVALID, "非法订单状态");
        };
    }

    private void invalidateProductCacheAfterCommit(Long productId) {
        if (TransactionSynchronizationManager.isSynchronizationActive()
                && TransactionSynchronizationManager.isActualTransactionActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    invalidateProductCache(productId);
                }
            });
            return;
        }
        invalidateProductCache(productId);
    }

    private void invalidateProductCache(Long productId) {
        try {
            cacheClient.delete(RedisKeys.productDetail(productId));
        } catch (RuntimeException e) {
            log.warn("Product detail cache invalidation failed: productId={}, error={}: {}",
                    productId, e.getClass().getSimpleName(), e.getMessage());
        }
        try {
            cacheClient.deleteByPattern(RedisKeys.productListPattern());
        } catch (RuntimeException e) {
            log.warn("Product list cache invalidation failed after normal order: productId={}, error={}: {}",
                    productId, e.getClass().getSimpleName(), e.getMessage());
        }
    }
}
