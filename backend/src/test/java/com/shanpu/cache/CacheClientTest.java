package com.shanpu.cache;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class CacheClientTest {

    private StringRedisTemplate redisTemplate;
    private ValueOperations<String, String> valueOperations;
    private ObjectMapper objectMapper;
    private CacheClient cacheClient;

    @BeforeEach
    void setUp() {
        redisTemplate = mock(StringRedisTemplate.class);
        valueOperations = mock(ValueOperations.class);
        objectMapper = new ObjectMapper();
        objectMapper.registerModule(new JavaTimeModule());
        cacheClient = new CacheClient(redisTemplate, objectMapper);
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
    }

    @Test
    void queryWithPassThrough_hit_returnsCachedValue() throws Exception {
        when(valueOperations.get("shanpu:test:1"))
                .thenReturn(objectMapper.writeValueAsString(new TestValue(1L, "cached")));

        TestValue value = cacheClient.queryWithPassThrough(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> new TestValue(id, "db"),
                Duration.ofMinutes(5),
                Duration.ofMinutes(1)
        );

        assertEquals(1L, value.getId());
        assertEquals("cached", value.getName());
        verify(valueOperations, never()).set(eq("shanpu:test:1"), anyString(), any(Duration.class));
    }

    @Test
    void queryWithPassThrough_miss_loadsDbAndWritesCache() {
        when(valueOperations.get("shanpu:test:1")).thenReturn(null);

        TestValue value = cacheClient.queryWithPassThrough(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> new TestValue(id, "db"),
                Duration.ofMinutes(5),
                Duration.ofMinutes(1)
        );

        assertEquals("db", value.getName());
        ArgumentCaptor<String> jsonCaptor = ArgumentCaptor.forClass(String.class);
        verify(valueOperations).set(eq("shanpu:test:1"), jsonCaptor.capture(), eq(Duration.ofMinutes(5)));
        assertTrue(jsonCaptor.getValue().contains("\"name\":\"db\""));
    }

    @Test
    void queryWithPassThrough_nullValue_writesEmptyPlaceholder() {
        when(valueOperations.get("shanpu:test:1")).thenReturn(null);

        TestValue value = cacheClient.queryWithPassThrough(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> null,
                Duration.ofMinutes(5),
                Duration.ofMinutes(1)
        );

        assertNull(value);
        verify(valueOperations).set("shanpu:test:1", "", Duration.ofMinutes(1));
    }

    @Test
    void queryWithPassThrough_emptyPlaceholder_skipsDb() {
        AtomicInteger fallbackCalls = new AtomicInteger();
        when(valueOperations.get("shanpu:test:1")).thenReturn("");

        TestValue value = cacheClient.queryWithPassThrough(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> {
                    fallbackCalls.incrementAndGet();
                    return new TestValue(id, "db");
                },
                Duration.ofMinutes(5),
                Duration.ofMinutes(1)
        );

        assertNull(value);
        assertEquals(0, fallbackCalls.get());
    }

    @Test
    void queryWithLogicalExpire_expired_returnsOldValueAndRebuilds() throws Exception {
        RedisData redisData = RedisData.builder()
                .expireTime(LocalDateTime.now().minusMinutes(1))
                .data(new TestValue(1L, "old"))
                .build();
        when(valueOperations.get("shanpu:test:1")).thenReturn(objectMapper.writeValueAsString(redisData));
        when(valueOperations.setIfAbsent("shanpu:test:lock:1", "1", Duration.ofSeconds(10))).thenReturn(true);

        TestValue value = cacheClient.queryWithLogicalExpire(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> new TestValue(id, "fresh"),
                Duration.ofMinutes(5)
        );

        assertEquals("old", value.getName());
        TimeUnit.MILLISECONDS.sleep(200);
        verify(redisTemplate).delete("shanpu:test:lock:1");
        ArgumentCaptor<String> jsonCaptor = ArgumentCaptor.forClass(String.class);
        verify(valueOperations).set(eq("shanpu:test:1"), jsonCaptor.capture());
        assertTrue(jsonCaptor.getValue().contains("\"name\":\"fresh\""));
    }

    @Getter
    @Setter
    @NoArgsConstructor
    @AllArgsConstructor
    static class TestValue {
        private Long id;
        private String name;
    }
}
