package com.shanpu.interceptor;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.util.JwtUtil;
import com.shanpu.common.util.UserContext;
import com.shanpu.infra.redis.RedisKeys;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.impl.DefaultClaims;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AuthInterceptorTest {

    @Mock
    private JwtUtil jwtUtil;

    @Mock
    private StringRedisTemplate redisTemplate;

    @InjectMocks
    private AuthInterceptor authInterceptor;

    @Mock
    private HttpServletRequest request;

    @Mock
    private ValueOperations<String, String> valueOperations;

    @AfterEach
    void tearDown() {
        UserContext.remove();
    }

    @Test
    void optionsRequest_skipsAuthorization() {
        when(request.getMethod()).thenReturn("OPTIONS");

        boolean result = authInterceptor.preHandle(request, null, null);

        assertTrue(result);
        verify(request, never()).getHeader("Authorization");
    }

    @Test
    void missingAuthorization_throwsUnauthorized() {
        when(request.getMethod()).thenReturn("GET");
        when(request.getHeader("Authorization")).thenReturn(null);
        assertThrows(BusinessException.class,
                () -> authInterceptor.preHandle(request, null, null));
    }

    @Test
    void wrongPrefix_throwsUnauthorized() {
        when(request.getMethod()).thenReturn("GET");
        when(request.getHeader("Authorization")).thenReturn("Token abc");
        assertThrows(BusinessException.class,
                () -> authInterceptor.preHandle(request, null, null));
    }

    @Test
    void invalidToken_throwsUnauthorized() {
        when(request.getMethod()).thenReturn("GET");
        when(request.getHeader("Authorization")).thenReturn("Bearer bad-token");
        when(jwtUtil.parse("bad-token")).thenThrow(new RuntimeException("bad"));
        assertThrows(BusinessException.class,
                () -> authInterceptor.preHandle(request, null, null));
    }

    @Test
    void sessionNotInRedis_throwsUnauthorized() {
        when(request.getMethod()).thenReturn("GET");
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(request.getHeader("Authorization")).thenReturn("Bearer valid-token");
        Claims claims = new DefaultClaims(Map.of("uid", 1L, "uname", "alice"));
        when(jwtUtil.parse("valid-token")).thenReturn(claims);
        String tokenHash = JwtUtil.tokenHash("valid-token");
        when(valueOperations.get(RedisKeys.userSession(tokenHash))).thenReturn(null);

        assertThrows(BusinessException.class,
                () -> authInterceptor.preHandle(request, null, null));
    }

    @Test
    void validSession_setsUserContext() {
        when(request.getMethod()).thenReturn("GET");
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(request.getHeader("Authorization")).thenReturn("Bearer good-token");
        Claims claims = new DefaultClaims(Map.of("uid", 42, "uname", "alice"));
        when(jwtUtil.parse("good-token")).thenReturn(claims);
        String tokenHash = JwtUtil.tokenHash("good-token");
        when(valueOperations.get(RedisKeys.userSession(tokenHash))).thenReturn("42");

        boolean result = authInterceptor.preHandle(request, null, null);
        assertTrue(result);
        assertEquals(42L, UserContext.get());
    }

    @Test
    void afterCompletion_removesUserContext() {
        UserContext.set(99L);
        authInterceptor.afterCompletion(null, null, null, null);
        assertEquals(null, UserContext.get());
    }
}
