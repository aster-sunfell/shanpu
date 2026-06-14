package com.shanpu.cache;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.shanpu.infra.redis.RedisKeys;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.LongAdder;

@Slf4j
@Component
public class ActivityLocalCache {

    private final ActivityLocalCacheProperties properties;
    private final Cache<String, String> cache;
    private final AtomicBoolean runtimeEnabled = new AtomicBoolean(true);
    private final LongAdder requestCount = new LongAdder();
    private final LongAdder hitCount = new LongAdder();

    public ActivityLocalCache(ActivityLocalCacheProperties properties) {
        this.properties = properties;
        this.runtimeEnabled.set(properties.isEnabled());
        this.cache = Caffeine.newBuilder()
                .maximumSize(properties.getMaxSize())
                .expireAfterWrite(Duration.ofMillis(properties.getExpireMs()))
                .build();
    }

    public String get(String key) {
        if (!isEnabled() || !supports(key)) {
            return null;
        }
        requestCount.increment();
        String value = cache.getIfPresent(key);
        if (value != null) {
            hitCount.increment();
        }
        return value;
    }

    public void put(String key, String value) {
        if (isEnabled() && supports(key)) {
            cache.put(key, value);
        }
    }

    public void invalidate(String key) {
        if (supports(key)) {
            cache.invalidate(key);
        }
    }

    public void invalidateAll() {
        cache.invalidateAll();
        cache.cleanUp();
    }

    public void resetStatsAndEntries() {
        invalidateAll();
        requestCount.reset();
        hitCount.reset();
    }

    public void setRuntimeEnabled(boolean enabled) {
        runtimeEnabled.set(enabled);
        if (!enabled) {
            invalidateAll();
        }
        log.info("Activity local cache runtime enabled={}", isEnabled());
    }

    public boolean enabled() {
        return isEnabled();
    }

    public double hitRate() {
        long requests = requestCount.sum();
        long hits = hitCount.sum();
        return requests == 0 ? 0.0 : (double) hits / requests;
    }

    public long requestCount() {
        return requestCount.sum();
    }

    public long size() {
        return cache.estimatedSize();
    }

    private boolean isEnabled() {
        return properties.isEnabled() && runtimeEnabled.get();
    }

    private boolean supports(String key) {
        return key != null
                && key.startsWith(RedisKeys.SECKILL_ACTIVITY_PREFIX)
                && !key.startsWith(RedisKeys.SECKILL_ACTIVITY_PREFIX + "lock:");
    }
}
