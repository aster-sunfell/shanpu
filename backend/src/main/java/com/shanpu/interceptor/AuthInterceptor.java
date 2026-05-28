package com.shanpu.interceptor;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.common.util.JwtUtil;
import com.shanpu.common.util.UserContext;
import com.shanpu.infra.redis.RedisKeys;
import io.jsonwebtoken.Claims;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpMethod;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

@Slf4j
@Component
@RequiredArgsConstructor
public class AuthInterceptor implements HandlerInterceptor {

    private final JwtUtil jwtUtil;
    private final StringRedisTemplate redisTemplate;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        if (HttpMethod.OPTIONS.matches(request.getMethod())) {
            return true;
        }

        String header = request.getHeader("Authorization");
        if (header == null || !header.startsWith("Bearer ")) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }

        String token = header.substring(7);
        Claims claims;
        try {
            claims = jwtUtil.parse(token);
        } catch (Exception e) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }

        String tokenHash = JwtUtil.tokenHash(token);
        String sessionUid = redisTemplate.opsForValue().get(RedisKeys.userSession(tokenHash));
        if (sessionUid == null) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }

        Long uid = extractUid(claims);
        UserContext.set(uid);
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                Object handler, Exception ex) {
        UserContext.remove();
    }

    private Long extractUid(Claims claims) {
        Object raw = claims.get("uid");
        if (raw instanceof Number n) {
            return n.longValue();
        }
        throw new BusinessException(ResultCode.UNAUTHORIZED);
    }
}
