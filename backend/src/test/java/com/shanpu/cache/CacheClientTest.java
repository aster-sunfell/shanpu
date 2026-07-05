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
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Iterator;
import java.util.List;
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
        cacheClient = new CacheClient(redisTemplate, objectMapper, new ActivityLocalCache(new ActivityLocalCacheProperties()));
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
    }

    @Test
    void queryWithCacheAside_hit_returnsCachedValue() throws Exception {
        when(valueOperations.get("shanpu:test:1"))
                .thenReturn(objectMapper.writeValueAsString(new TestValue(1L, "cached")));

        TestValue value = cacheClient.queryWithCacheAside(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> new TestValue(id, "db"),
                Duration.ofMinutes(5)
        );

        assertEquals(1L, value.getId());
        assertEquals("cached", value.getName());
        verify(valueOperations, never()).set(eq("shanpu:test:1"), anyString(), any(Duration.class));
    }

    @Test
    void queryWithCacheAside_miss_loadsDbAndWritesCache() {
        when(valueOperations.get("shanpu:test:1")).thenReturn(null);

        TestValue value = cacheClient.queryWithCacheAside(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> new TestValue(id, "db"),
                Duration.ofMinutes(5)
        );

        assertEquals("db", value.getName());
        ArgumentCaptor<String> jsonCaptor = ArgumentCaptor.forClass(String.class);
        verify(valueOperations).set(eq("shanpu:test:1"), jsonCaptor.capture(), eq(Duration.ofMinutes(5)));
        assertTrue(jsonCaptor.getValue().contains("\"name\":\"db\""));
    }

    @Test
    void queryWithNullCache_nullPlaceholderHit_skipsValueSupplier() {
        AtomicInteger fallbackCalls = new AtomicInteger();
        when(valueOperations.get("shanpu:test:null:1")).thenReturn("");

        TestValue value = cacheClient.queryWithNullCache(
                "shanpu:test:null:1",
                () -> {
                    fallbackCalls.incrementAndGet();
                    return new TestValue(1L, "db");
                },
                Duration.ofMinutes(1)
        );

        assertNull(value);
        assertEquals(0, fallbackCalls.get());
    }

    @Test
    void queryWithNullCache_nullValue_writesOnlyNullPlaceholder() {
        when(valueOperations.get("shanpu:test:null:1")).thenReturn(null);

        TestValue value = cacheClient.queryWithNullCache(
                "shanpu:test:null:1",
                () -> null,
                Duration.ofMinutes(1)
        );

        assertNull(value);
        verify(valueOperations).set("shanpu:test:null:1", "", Duration.ofMinutes(1));
        verify(valueOperations, never()).set(eq("shanpu:test:1"), eq(""), any(Duration.class));
    }

    @Test
    void queryWithMutex_nullValue_skipsEmptyPlaceholder() {
        when(valueOperations.setIfAbsent("shanpu:order:lock:1", "1", Duration.ofSeconds(10))).thenReturn(true);

        TestValue value = cacheClient.queryWithMutex(
                "shanpu:order:1",
                cacheClient.javaType(TestValue.class),
                () -> null,
                "shanpu:order:lock:1",
                Duration.ofSeconds(30)
        );

        assertNull(value);
        verify(valueOperations, never()).set(eq("shanpu:order:1"), eq(""), any(Duration.class));
    }

    @Test
    void queryWithLogicalExpire_nullValue_skipsEmptyPlaceholder() {
        when(valueOperations.get("shanpu:test:1")).thenReturn(null);

        TestValue value = cacheClient.queryWithLogicalExpire(
                "shanpu:test:",
                1L,
                TestValue.class,
                id -> null,
                Duration.ofMinutes(5)
        );

        assertNull(value);
        verify(valueOperations, never()).set(eq("shanpu:test:1"), eq(""), any(Duration.class));
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

    @Test
    void queryWithLogicalExpire_activityLocalCacheHit_skipsRedisAfterFirstRead() throws Exception {
        RedisData redisData = RedisData.builder()
                .expireTime(LocalDateTime.now().plusMinutes(1))
                .data(new TestValue(1L, "activity"))
                .build();
        when(valueOperations.get("shanpu:seckill:activity:1"))
                .thenReturn(objectMapper.writeValueAsString(redisData));

        TestValue first = cacheClient.queryWithLogicalExpire(
                "shanpu:seckill:activity:",
                1L,
                TestValue.class,
                id -> new TestValue(id, "db"),
                Duration.ofMinutes(5)
        );
        TestValue second = cacheClient.queryWithLogicalExpire(
                "shanpu:seckill:activity:",
                1L,
                TestValue.class,
                id -> new TestValue(id, "db"),
                Duration.ofMinutes(5)
        );

        assertEquals("activity", first.getName());
        assertEquals("activity", second.getName());
        verify(valueOperations).get("shanpu:seckill:activity:1");
    }

    @Test
    void deleteByPattern_scansAndDeletesMatchedKeys() {
        when(redisTemplate.scan(any(ScanOptions.class))).thenReturn(new ListCursor(List.of(
                "shanpu:product:list:page:1:size:8",
                "shanpu:product:list:page:1:size:12"
        )));

        cacheClient.deleteByPattern("shanpu:product:list:*");

        verify(redisTemplate).delete(List.of(
                "shanpu:product:list:page:1:size:8",
                "shanpu:product:list:page:1:size:12"
        ));
    }

    @Getter
    @Setter
    @NoArgsConstructor
    @AllArgsConstructor
    static class TestValue {
        private Long id;
        private String name;
    }

    private static class ListCursor implements Cursor<String> {
        private final Iterator<String> iterator;

        private ListCursor(List<String> keys) {
            this.iterator = keys.iterator();
        }

        @Override
        public boolean isClosed() {
            return false;
        }

        @Override
        public long getCursorId() {
            return 0;
        }

        @Override
        public long getPosition() {
            return 0;
        }

        @Override
        public boolean hasNext() {
            return iterator.hasNext();
        }

        @Override
        public String next() {
            return iterator.next();
        }

        @Override
        public void close() {
        }

    }
}
