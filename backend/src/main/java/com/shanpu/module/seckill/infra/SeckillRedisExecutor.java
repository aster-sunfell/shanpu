package com.shanpu.module.seckill.infra;

import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.seckill.domain.entity.SeckillActivity;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

@Component
@RequiredArgsConstructor
public class SeckillRedisExecutor {

    public static final long OK = 0L;
    public static final long STOCK_SHORTAGE = 1L;
    public static final long DUPLICATE = 2L;
    public static final long NOT_WARMED = 3L;

    private final StringRedisTemplate redisTemplate;
    private final DefaultRedisScript<Long> seckillScript;
    private final DefaultRedisScript<Long> seckillRollbackScript;

    public Long preDeduct(Long activityId, Long userId, String orderId) {
        return redisTemplate.execute(
                seckillScript,
                List.of(RedisKeys.seckillStock(activityId), RedisKeys.seckillBought(activityId)),
                String.valueOf(userId),
                orderId
        );
    }

    public void rollback(Long activityId, Long userId) {
        redisTemplate.execute(
                seckillRollbackScript,
                List.of(RedisKeys.seckillStock(activityId), RedisKeys.seckillBought(activityId)),
                String.valueOf(userId)
        );
    }

    public void ensureStock(SeckillActivityVO activity) {
        if (activity.getTier() == null || activity.getTier() == SeckillActivity.TIER_INCLUSIVE) {
            return;
        }
        Duration ttl = redisTtl(activity.getEndAt());
        redisTemplate.opsForValue().setIfAbsent(
                RedisKeys.seckillStock(activity.getId()),
                String.valueOf(activity.getTotalStock()),
                ttl
        );
        redisTemplate.opsForSet().add(RedisKeys.seckillBought(activity.getId()), "__warmup__");
        redisTemplate.expire(RedisKeys.seckillBought(activity.getId()), ttl);
    }

    public Integer currentStock(SeckillActivityVO activity) {
        if (activity.getTier() == null || activity.getTier() == SeckillActivity.TIER_INCLUSIVE) {
            return activity.getTotalStock();
        }
        String value = redisTemplate.opsForValue().get(RedisKeys.seckillStock(activity.getId()));
        if (value == null || value.isBlank()) {
            return activity.getTotalStock();
        }
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException e) {
            return activity.getTotalStock();
        }
    }

    public void setStock(Long activityId, int stock) {
        redisTemplate.opsForValue().set(RedisKeys.seckillStock(activityId), String.valueOf(stock));
    }

    private Duration redisTtl(LocalDateTime endAt) {
        if (endAt == null) {
            return Duration.ofHours(2);
        }
        Duration ttl = Duration.between(LocalDateTime.now(), endAt.plusHours(1));
        return !ttl.isNegative() && !ttl.isZero() ? ttl : Duration.ofHours(1);
    }
}
