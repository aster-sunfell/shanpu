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

/**
 * 执行 {@link RateLimit} 注解声明的滑动窗口限流。
 *
 * <p>限流状态集中保存在 Redis ZSet 中，切面只负责提取业务维度、组装 Key
 * 和把 Lua 返回值转换为接口异常。</p>
 */
@Aspect
@Component
@RequiredArgsConstructor
public class RateLimitAspect {

    private final StringRedisTemplate redisTemplate;
    private final DefaultRedisScript<Long> rateLimitScript;

    /**
     * 在目标接口方法执行前完成限流判断。
     *
     * @param joinPoint 当前被拦截的方法调用
     * @param rateLimit 方法上的限流配置
     */
    @Before("@annotation(rateLimit)")
    public void before(JoinPoint joinPoint, RateLimit rateLimit) {
        // 使用当前毫秒时间作为滑动窗口计分基准，Lua 内按窗口长度清理过期请求。
        long now = System.currentTimeMillis();
        // Redis Key 由限流维度和值加接口标识组成，确保不同用户、IP、接口互不串窗。
        String key = RedisKeys.rateLimit(resolveDimensionValue(rateLimit.dim()), resolveApiKey(joinPoint, rateLimit));
        // Lua 脚本在 Redis 单线程内完成清理、计数、写入，避免多实例并发下出现超放。
        Long allowed = redisTemplate.execute(
                rateLimitScript,
                List.of(key),
                String.valueOf(now),
                String.valueOf(rateLimit.window()),
                String.valueOf(rateLimit.max()),
                now + ":" + UUID.randomUUID()
        );
        // 脚本返回 1 表示放行，其他结果统一视为触发限流，由全局异常处理器映射为 HTTP 429。
        if (!Long.valueOf(1L).equals(allowed)) {
            throw new BusinessException(ResultCode.RATE_LIMITED, "操作过于频繁");
        }
    }

    /**
     * 解析限流维度在 Redis Key 中的业务值。
     *
     * @param dimension 注解声明的限流维度
     * @return 形如 {@code user:1}、{@code ip:127.0.0.1} 或 {@code api} 的维度值
     */
    private String resolveDimensionValue(RateLimit.Dimension dimension) {
        return switch (dimension) {
            // 用户维度依赖鉴权拦截器提前写入 UserContext，适合下单和抢购类接口。
            case USER -> "user:" + UserContext.requireUserId();
            // IP 维度不依赖登录态，适合注册和登录等匿名入口。
            case IP -> "ip:" + clientIp();
            // API 维度所有调用方共享同一个窗口，用于保护全局热点接口。
            case API -> "api";
        };
    }

    /**
     * 解析接口在 Redis Key 中的后缀。
     *
     * @param joinPoint 当前被拦截的方法调用
     * @param rateLimit 方法上的限流配置
     * @return 自定义 key 或默认方法签名
     */
    private String resolveApiKey(JoinPoint joinPoint, RateLimit rateLimit) {
        if (!rateLimit.key().isBlank()) {
            // 自定义 key 用于多个方法共用同一个限流桶，或隐藏过长的方法签名。
            return rateLimit.key();
        }
        MethodSignature signature = (MethodSignature) joinPoint.getSignature();
        // 默认使用类名加方法名，避免同名接口方法之间共享限流窗口。
        return signature.getDeclaringTypeName() + "." + signature.getMethod().getName();
    }

    /**
     * 解析当前请求的客户端 IP。
     *
     * <p>优先信任反向代理写入的 {@code X-Forwarded-For} 首个地址，再回退到
     * {@code X-Real-IP} 和 Servlet 远端地址。</p>
     *
     * @return 客户端 IP，非 HTTP 上下文中返回 {@code unknown}
     */
    private String clientIp() {
        ServletRequestAttributes attrs =
                (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        if (attrs == null) {
            // 非 Web 调用理论上不会触发 IP 限流，保底值避免空指针影响切面执行。
            return "unknown";
        }
        HttpServletRequest request = attrs.getRequest();
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            // 多级代理会用逗号追加地址，最左侧地址代表原始客户端。
            return forwarded.split(",")[0].trim();
        }
        String realIp = request.getHeader("X-Real-IP");
        if (realIp != null && !realIp.isBlank()) {
            // 单层代理常用 X-Real-IP 透传客户端地址。
            return realIp.trim();
        }
        // 本地开发或无代理部署时直接使用 Servlet 容器看到的远端地址。
        return request.getRemoteAddr();
    }
}
