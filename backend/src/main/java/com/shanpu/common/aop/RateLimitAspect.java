package com.shanpu.common.aop;

import com.shanpu.common.annotation.RateLimit;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.common.util.UserContext;
import com.shanpu.infra.redis.RedisKeys;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.aspectj.lang.JoinPoint;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.util.List;
import java.util.UUID;

@Aspect
@Component
@RequiredArgsConstructor
public class RateLimitAspect {

    private final StringRedisTemplate redisTemplate;
    private final DefaultRedisScript<Long> rateLimitScript;

    @Before("@annotation(rateLimit)")
    public void before(JoinPoint joinPoint, RateLimit rateLimit) {
        long now = System.currentTimeMillis();
        String key = RedisKeys.rateLimit(resolveDimensionValue(rateLimit.dim()), resolveApiKey(joinPoint, rateLimit));
        Long allowed = redisTemplate.execute(
                rateLimitScript,
                List.of(key),
                String.valueOf(now),
                String.valueOf(rateLimit.window()),
                String.valueOf(rateLimit.max()),
                now + ":" + UUID.randomUUID()
        );
        if (!Long.valueOf(1L).equals(allowed)) {
            throw new BusinessException(ResultCode.RATE_LIMITED, "操作过于频繁");
        }
    }

    private String resolveDimensionValue(RateLimit.Dimension dimension) {
        return switch (dimension) {
            case USER -> "user:" + UserContext.requireUserId();
            case IP -> "ip:" + clientIp();
            case API -> "api";
        };
    }

    private String resolveApiKey(JoinPoint joinPoint, RateLimit rateLimit) {
        if (!rateLimit.key().isBlank()) {
            return rateLimit.key();
        }
        MethodSignature signature = (MethodSignature) joinPoint.getSignature();
        return signature.getDeclaringTypeName() + "." + signature.getMethod().getName();
    }

    private String clientIp() {
        ServletRequestAttributes attrs =
                (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        if (attrs == null) {
            return "unknown";
        }
        HttpServletRequest request = attrs.getRequest();
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            return forwarded.split(",")[0].trim();
        }
        String realIp = request.getHeader("X-Real-IP");
        if (realIp != null && !realIp.isBlank()) {
            return realIp.trim();
        }
        return request.getRemoteAddr();
    }
}
