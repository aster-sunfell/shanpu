package com.shanpu.module.seckill.service.impl;

import cn.hutool.core.lang.Snowflake;
import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.domain.vo.SeckillGrabVO;
import com.shanpu.module.seckill.infra.SeckillProducer;
import com.shanpu.module.seckill.infra.SeckillRedisExecutor;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import com.shanpu.module.seckill.service.SeckillService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class SeckillServiceImpl implements SeckillService {

    private static final Duration ACTIVITY_TTL = Duration.ofMinutes(10);
    private static final Duration ACTIVITY_NULL_TTL = Duration.ofMinutes(2);

    private final SeckillActivityMapper activityMapper;
    private final CacheClient cacheClient;
    private final SeckillRedisExecutor redisExecutor;
    private final SeckillProducer seckillProducer;
    private final SeckillOrderWriter orderWriter;
    private final Snowflake snowflake;

    @Override
    public List<SeckillActivityVO> list(Integer tier, String status) {
        Integer statusCode = toStatusCode(status);
        return activityMapper.selectList(tier, statusCode).stream()
                .map(this::withCurrentStock)
                .toList();
    }

    @Override
    public SeckillActivityVO detail(Long activityId) {
        SeckillActivityVO activity = cacheClient.queryWithLogicalExpire(
                RedisKeys.SECKILL_ACTIVITY_PREFIX,
                activityId,
                SeckillActivityVO.class,
                activityMapper::selectDetailById,
                ACTIVITY_TTL,
                ACTIVITY_NULL_TTL
        );
        if (activity == null) {
            throw new BusinessException(ResultCode.NOT_FOUND);
        }
        return withCurrentStock(activity);
    }

    @Override
    public SeckillGrabVO grab(Long userId, Long activityId) {
        SeckillActivityVO activity = detail(activityId);
        validateOpen(activity);

        String orderId = snowflake.nextIdStr();
        return switch (activity.getTier()) {
            case SeckillActivity.TIER_INCLUSIVE -> grabInclusive(userId, orderId, activity);
            case SeckillActivity.TIER_FLASH -> grabFlash(userId, orderId, activity);
            case SeckillActivity.TIER_SUPER -> grabSuper(userId, orderId, activity);
            default -> throw new BusinessException(ResultCode.PARAM_INVALID, "非法秒杀档位");
        };
    }

    private SeckillGrabVO grabInclusive(Long userId, String orderId, SeckillActivityVO activity) {
        orderWriter.createSyncPaidOrder(
                userId,
                orderId,
                activity.getId(),
                activity.getProductId(),
                activity.getSeckillPrice(),
                Order.SOURCE_SECKILL_INCLUSIVE
        );
        return paid(orderId, activity.getTier());
    }

    private SeckillGrabVO grabFlash(Long userId, String orderId, SeckillActivityVO activity) {
        preDeductOrThrow(activity.getId(), userId, orderId);
        try {
            orderWriter.createSyncPaidOrder(
                    userId,
                    orderId,
                    activity.getId(),
                    activity.getProductId(),
                    activity.getSeckillPrice(),
                    Order.SOURCE_SECKILL_FLASH
            );
            return paid(orderId, activity.getTier());
        } catch (RuntimeException e) {
            redisExecutor.rollback(activity.getId(), userId);
            throw e;
        }
    }

    private SeckillGrabVO grabSuper(Long userId, String orderId, SeckillActivityVO activity) {
        preDeductOrThrow(activity.getId(), userId, orderId);
        SeckillOrderMsg msg = SeckillOrderMsg.builder()
                .orderId(orderId)
                .userId(userId)
                .activityId(activity.getId())
                .productId(activity.getProductId())
                .price(activity.getSeckillPrice())
                .source(Order.SOURCE_SECKILL_SUPER)
                .createdAt(LocalDateTime.now())
                .build();
        try {
            seckillProducer.send(msg);
            return SeckillGrabVO.builder()
                    .orderId(orderId)
                    .status("PENDING")
                    .tier(activity.getTier())
                    .build();
        } catch (RuntimeException e) {
            redisExecutor.rollback(activity.getId(), userId);
            throw e;
        }
    }

    private void preDeductOrThrow(Long activityId, Long userId, String orderId) {
        Long result = redisExecutor.preDeduct(activityId, userId, orderId);
        if (Long.valueOf(SeckillRedisExecutor.OK).equals(result)) {
            return;
        }
        if (Long.valueOf(SeckillRedisExecutor.DUPLICATE).equals(result)) {
            throw new BusinessException(ResultCode.DUPLICATE_ORDER, "该活动您已参与");
        }
        if (Long.valueOf(SeckillRedisExecutor.NOT_WARMED).equals(result)) {
            SeckillActivityVO activity = activityMapper.selectDetailById(activityId);
            if (activity != null) {
                redisExecutor.ensureStock(activity);
                Long retry = redisExecutor.preDeduct(activityId, userId, orderId);
                if (Long.valueOf(SeckillRedisExecutor.OK).equals(retry)) {
                    return;
                }
                result = retry;
            }
        }
        if (Long.valueOf(SeckillRedisExecutor.DUPLICATE).equals(result)) {
            throw new BusinessException(ResultCode.DUPLICATE_ORDER, "该活动您已参与");
        }
        if (Long.valueOf(SeckillRedisExecutor.STOCK_SHORTAGE).equals(result)) {
            throw new BusinessException(ResultCode.STOCK_SHORTAGE, "已售罄");
        }
        throw new BusinessException(ResultCode.INTERNAL_ERROR, "秒杀活动尚未预热");
    }

    private SeckillGrabVO paid(String orderId, Integer tier) {
        return SeckillGrabVO.builder()
                .orderId(orderId)
                .status("PAID")
                .tier(tier)
                .build();
    }

    private void validateOpen(SeckillActivityVO activity) {
        LocalDateTime now = LocalDateTime.now();
        if (activity.getStartAt() != null && activity.getStartAt().isAfter(now)) {
            throw new BusinessException(ResultCode.ACTIVITY_NOT_START);
        }
        if (activity.getEndAt() != null && !activity.getEndAt().isAfter(now)) {
            throw new BusinessException(ResultCode.ACTIVITY_ENDED);
        }
        if (activity.getStatusCode() != null && activity.getStatusCode() == SeckillActivity.STATUS_ENDED) {
            throw new BusinessException(ResultCode.ACTIVITY_ENDED);
        }
    }

    private SeckillActivityVO withCurrentStock(SeckillActivityVO activity) {
        activity.setStock(redisExecutor.currentStock(activity));
        return activity;
    }

    private Integer toStatusCode(String status) {
        if (status == null || status.isBlank()) {
            return null;
        }
        return switch (status) {
            case "UPCOMING" -> SeckillActivity.STATUS_UPCOMING;
            case "ACTIVE" -> SeckillActivity.STATUS_ACTIVE;
            case "ENDED" -> SeckillActivity.STATUS_ENDED;
            default -> throw new BusinessException(ResultCode.PARAM_INVALID, "非法活动状态");
        };
    }
}
