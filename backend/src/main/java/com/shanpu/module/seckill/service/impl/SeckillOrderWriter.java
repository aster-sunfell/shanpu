package com.shanpu.module.seckill.service.impl;

import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import com.shanpu.module.user.domain.entity.PointLog;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;

/**
 * 秒杀订单写入器。
 *
 * <p>同步档位和 Kafka 异步消费最终都通过本类完成数据库事实写入，保证扣活动库存、
 * 扣用户积分、写订单和写积分流水的事务边界一致。</p>
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class SeckillOrderWriter {

    private final SeckillActivityMapper activityMapper;
    private final OrderMapper orderMapper;
    private final UserMapper userMapper;
    private final PointLogMapper pointLogMapper;
    private final TransactionTemplate transactionTemplate;
    private final CacheClient cacheClient;

    /**
     * 创建同步支付完成的秒杀订单。
     *
     * <p>普惠档直接调用该方法；限时档在 Redis + Lua 预扣减成功后调用该方法。
     * 方法在一个数据库事务内扣活动库存、扣用户积分、写订单和写积分流水。</p>
     *
     * @param userId 当前登录用户 ID
     * @param orderId 订单号
     * @param activityId 秒杀活动 ID
     * @param productId 商品 ID
     * @param price 秒杀积分价
     * @param source 订单来源
     */
    public void createSyncPaidOrder(Long userId, String orderId, Long activityId,
                                    Long productId, int price, int source) {
        transactionTemplate.executeWithoutResult(status -> {
            // 数据库库存仍要扣减一次，作为 Redis 预扣后的最终落库事实。
            if (activityMapper.deductStock(activityId) == 0) {
                throw new BusinessException(ResultCode.STOCK_SHORTAGE);
            }
            // 积分不足时事务回滚，限时档上层会回滚 Redis 预扣减。
            if (userMapper.deductPoints(userId, price) == 0) {
                throw new BusinessException(ResultCode.POINTS_NOT_ENOUGH);
            }
            insertOrder(userId, orderId, activityId, productId, price, source);
            insertPointLog(userId, orderId, price);
        });
        invalidateOrderCache(userId, orderId);
    }

    /**
     * 消费 Kafka 消息并创建异步支付完成的超级秒杀订单。
     *
     * <p>超级档接口返回 PENDING 后由消费端调用该方法。方法先做订单号幂等判断，
     * 再在同一数据库事务中写订单、扣活动库存、扣积分和写积分流水。</p>
     *
     * @param msg 秒杀订单消息
     * @return true 表示本次实际创建订单，false 表示重复消息已被跳过
     */
    public boolean createAsyncPaidOrder(SeckillOrderMsg msg) {
        Boolean created = transactionTemplate.execute(status -> {
            // 订单号已存在说明这条消息已经消费成功过，本次重投直接幂等结束。
            if (orderMapper.existsByOrderId(msg.getOrderId()) > 0) {
                return false;
            }
            // 先插入订单拿到幂等落点；后续任一步失败都会随事务回滚。
            int inserted = insertOrderIgnore(msg);
            if (inserted == 0) {
                // insert ignore 返回 0 可能是订单号重复，也可能是一人一单唯一键冲突。
                if (orderMapper.existsByOrderId(msg.getOrderId()) > 0) {
                    return false;
                }
                throw new BusinessException(ResultCode.DUPLICATE_ORDER);
            }
            // Redis 预扣只是前置削峰，MySQL 库存扣减仍是订单最终成立的事实记录。
            if (activityMapper.deductStock(msg.getActivityId()) == 0) {
                throw new BusinessException(ResultCode.STOCK_SHORTAGE);
            }
            // 积分扣减失败会触发消费重试，最终由 DLQ/补偿任务兜底。
            if (userMapper.deductPoints(msg.getUserId(), msg.getPrice()) == 0) {
                throw new BusinessException(ResultCode.POINTS_NOT_ENOUGH);
            }
            insertPointLog(msg.getUserId(), msg.getOrderId(), msg.getPrice());
            return true;
        });
        boolean orderCreated = Boolean.TRUE.equals(created);
        if (orderCreated) {
            invalidateOrderCache(msg.getUserId(), msg.getOrderId());
        }
        return orderCreated;
    }

    /**
     * 插入同步链路订单。
     *
     * <p>数据库唯一索引用于兜底一人一单，重复购买会转换为业务异常。</p>
     *
     * @param userId 当前登录用户 ID
     * @param orderId 订单号
     * @param activityId 秒杀活动 ID
     * @param productId 商品 ID
     * @param price 秒杀积分价
     * @param source 订单来源
     */
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

    /**
     * 以忽略重复键的方式插入异步链路订单。
     *
     * <p>Kafka 可能重复投递同一条消息，insert ignore 让消费端可以用返回行数判断幂等结果。</p>
     *
     * @param msg 秒杀订单消息
     * @return 插入行数，0 表示订单号或唯一索引冲突
     */
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

    /**
     * 写入订单消费积分流水。
     *
     * @param userId 用户 ID
     * @param orderId 订单号
     * @param price 本次扣减积分
     */
    private void insertPointLog(Long userId, String orderId, int price) {
        // 扣积分已经在同一事务内完成，这里读取扣减后的余额写入流水快照。
        Integer balanceAfter = userMapper.selectPointsById(userId);
        PointLog pointLog = new PointLog();
        pointLog.setUserId(userId);
        pointLog.setDelta(-price);
        pointLog.setBalanceAfter(balanceAfter);
        pointLog.setBizType(PointLog.BIZ_ORDER_CONSUME);
        pointLog.setBizRef(orderId);
        pointLogMapper.insert(pointLog);
    }

    /**
     * 删除订单详情缓存。
     *
     * <p>缓存删除结果与已经提交的订单事务结果解耦。</p>
     *
     * @param userId 用户 ID
     * @param orderId 订单号
     */
    private void invalidateOrderCache(Long userId, String orderId) {
        try {
            cacheClient.delete(RedisKeys.orderDetail(userId, orderId));
        } catch (RuntimeException e) {
            log.warn("Order detail cache invalidation failed after seckill order write: orderId={}, error={}: {}",
                    orderId, e.getClass().getSimpleName(), e.getMessage());
        }
    }
}
