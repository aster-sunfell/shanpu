package com.shanpu.cache;

import com.fasterxml.jackson.databind.JavaType;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shanpu.infra.redis.RedisKeys;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;
import java.util.function.Supplier;

@Slf4j
@Component
@RequiredArgsConstructor
public class CacheClient {

    private static final String EMPTY_VALUE = "";
    private static final Duration LOCK_TTL = Duration.ofSeconds(10);
    private static final ExecutorService CACHE_REBUILD_EXECUTOR = Executors.newFixedThreadPool(4);

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final ActivityLocalCache activityLocalCache;

    public void set(String key, Object value, Duration ttl) {
        try {
            String json = objectMapper.writeValueAsString(value);
            redisTemplate.opsForValue().set(key, json, ttl);
            activityLocalCache.put(key, json);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to serialize cache value for key " + key, e);
        }
    }

    public void setWithLogicalExpire(String key, Object value, Duration ttl) {
        RedisData redisData = RedisData.builder()
                .data(value)
                .expireTime(LocalDateTime.now().plus(ttl))
                .build();
        try {
            String json = objectMapper.writeValueAsString(redisData);
            redisTemplate.opsForValue().set(key, json);
            activityLocalCache.put(key, json);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to serialize logical cache value for key " + key, e);
        }
    }

    public void delete(String key) {
        redisTemplate.delete(key);
        activityLocalCache.invalidate(key);
    }

    public void deleteByPattern(String pattern) {
        if (pattern != null && pattern.startsWith(RedisKeys.SECKILL_ACTIVITY_PREFIX)) {
            activityLocalCache.invalidateAll();
        }
        ScanOptions options = ScanOptions.scanOptions().match(pattern).count(100).build();
        List<String> batch = new ArrayList<>(100);
        try (Cursor<String> cursor = redisTemplate.scan(options)) {
            while (cursor.hasNext()) {
                batch.add(cursor.next());
                if (batch.size() == 100) {
                    redisTemplate.delete(batch);
                    batch.clear();
                }
            }
            if (!batch.isEmpty()) {
                redisTemplate.delete(batch);
            }
        } catch (RuntimeException e) {
            throw new IllegalStateException("Failed to scan cache keys for pattern " + pattern, e);
        }
    }

    public <R, ID> R queryWithPassThrough(String keyPrefix, ID id, Class<R> type,
                                          Function<ID, R> dbFallback,
                                          Duration ttl, Duration nullTtl) {
        return queryWithPassThrough(keyPrefix + id, javaType(type), () -> dbFallback.apply(id), ttl, nullTtl);
    }

    public <R> R queryWithPassThrough(String key, JavaType type, Supplier<R> dbFallback,
                                      Duration ttl, Duration nullTtl) {
        String json = getCacheValue(key);
        if (json != null) {
            return json.isEmpty() ? null : read(json, type);
        }

        R value = dbFallback.get();
        if (value == null) {
            redisTemplate.opsForValue().set(key, EMPTY_VALUE, nullTtl);
            activityLocalCache.put(key, EMPTY_VALUE);
            return null;
        }
        set(key, value, ttl);
        return value;
    }

    public <R, ID> R queryWithLogicalExpire(String keyPrefix, ID id, Class<R> type,
                                            Function<ID, R> dbFallback,
                                            Duration ttl) {
        return queryWithLogicalExpire(keyPrefix, id, type, dbFallback, ttl, null);
    }

    public <R, ID> R queryWithLogicalExpire(String keyPrefix, ID id, Class<R> type,
                                            Function<ID, R> dbFallback,
                                            Duration ttl, Duration nullTtl) {
        return queryWithLogicalExpire(keyPrefix + id, javaType(type), () -> dbFallback.apply(id),
                keyPrefix + "lock:" + id, ttl, nullTtl);
    }

    public <R> R queryWithLogicalExpire(String key, JavaType type, Supplier<R> dbFallback,
                                        String lockKey, Duration ttl) {
        return queryWithLogicalExpire(key, type, dbFallback, lockKey, ttl, null);
    }

    public <R> R queryWithLogicalExpire(String key, JavaType type, Supplier<R> dbFallback,
                                        String lockKey, Duration ttl, Duration nullTtl) {
        String json = getCacheValue(key);
        if (json != null && json.isEmpty()) {
            return null;
        }
        if (json == null) {
            R value = dbFallback.get();
            if (value == null) {
                if (nullTtl != null) {
                    redisTemplate.opsForValue().set(key, EMPTY_VALUE, nullTtl);
                    activityLocalCache.put(key, EMPTY_VALUE);
                }
                return null;
            }
            setWithLogicalExpire(key, value, ttl);
            return value;
        }

        RedisData redisData = read(json, RedisData.class);
        R value = objectMapper.convertValue(redisData.getData(), type);
        if (redisData.getExpireTime() == null || redisData.getExpireTime().isAfter(LocalDateTime.now())) {
            return value;
        }

        if (tryLock(lockKey)) {
            CACHE_REBUILD_EXECUTOR.submit(() -> {
                try {
                    R latest = dbFallback.get();
                    if (latest != null) {
                        setWithLogicalExpire(key, latest, ttl);
                    }
                } catch (Exception e) {
                    log.warn("Logical cache rebuild failed: key={}", key, e);
                } finally {
                    unlock(lockKey);
                }
            });
        }
        return value;
    }

    public <R, ID> R queryWithMutex(String keyPrefix, ID id, Class<R> type,
                                    Function<ID, R> dbFallback,
                                    Duration ttl, Duration nullTtl) {
        return queryWithMutex(keyPrefix + id, javaType(type), () -> dbFallback.apply(id),
                keyPrefix + "lock:" + id, ttl, nullTtl);
    }

    public <R> R queryWithMutex(String key, JavaType type, Supplier<R> dbFallback,
                                String lockKey, Duration ttl, Duration nullTtl) {
        String json = getCacheValue(key);
        if (json != null) {
            return json.isEmpty() ? null : read(json, type);
        }

        boolean locked = false;
        try {
            for (int i = 0; i < 3; i++) {
                locked = tryLock(lockKey);
                if (locked) {
                    String doubleCheck = redisTemplate.opsForValue().get(key);
                    if (doubleCheck != null) {
                        activityLocalCache.put(key, doubleCheck);
                        return doubleCheck.isEmpty() ? null : read(doubleCheck, type);
                    }
                    R value = dbFallback.get();
                    if (value == null) {
                        redisTemplate.opsForValue().set(key, EMPTY_VALUE, nullTtl);
                        activityLocalCache.put(key, EMPTY_VALUE);
                        return null;
                    }
                    set(key, value, ttl);
                    return value;
                }
                TimeUnit.MILLISECONDS.sleep(50);
                json = redisTemplate.opsForValue().get(key);
                if (json != null) {
                    activityLocalCache.put(key, json);
                    return json.isEmpty() ? null : read(json, type);
                }
            }
            R value = dbFallback.get();
            if (value == null) {
                redisTemplate.opsForValue().set(key, EMPTY_VALUE, nullTtl);
                activityLocalCache.put(key, EMPTY_VALUE);
                return null;
            }
            set(key, value, ttl);
            return value;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while rebuilding cache for key " + key, e);
        } finally {
            if (locked) {
                unlock(lockKey);
            }
        }
    }

    public JavaType javaType(Class<?> rawClass, Class<?>... parameterClasses) {
        if (parameterClasses.length == 0) {
            return objectMapper.getTypeFactory().constructType(rawClass);
        }
        return objectMapper.getTypeFactory().constructParametricType(rawClass, parameterClasses);
    }

    private boolean tryLock(String key) {
        Boolean ok = redisTemplate.opsForValue().setIfAbsent(key, "1", LOCK_TTL);
        return Boolean.TRUE.equals(ok);
    }

    private void unlock(String key) {
        redisTemplate.delete(key);
    }

    private String getCacheValue(String key) {
        String local = activityLocalCache.get(key);
        if (local != null) {
            return local;
        }
        String json = redisTemplate.opsForValue().get(key);
        if (json != null) {
            activityLocalCache.put(key, json);
        }
        return json;
    }

    private <T> T read(String json, Class<T> type) {
        try {
            return objectMapper.readValue(json, type);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to deserialize cache value", e);
        }
    }

    private <T> T read(String json, JavaType type) {
        try {
            return objectMapper.readValue(json, type);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to deserialize cache value", e);
        }
    }
}
