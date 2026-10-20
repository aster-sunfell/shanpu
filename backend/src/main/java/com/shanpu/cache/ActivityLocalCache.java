package com.shanpu.cache;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.shanpu.infra.redis.RedisKeys;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.LongAdder;

/**
 * 秒杀活动详情的本地 L1 缓存。
 *
 * <p>本类只缓存 {@code shanpu:seckill:activity:{id}} 这一类活动详情 JSON，
 * 不缓存库存、已购集合、限流、登录态和锁 Key。活动详情是抢购前置查询的热点读
 * Key，适合用 Caffeine 在单 JVM 内承接高频读请求；强一致读写 Key 仍直接访问 Redis。</p>
 *
 * <p>运行时开关服务于 M6 缓存层级压测。关闭 L1 时会清空本地条目，后续请求
 * 回退到纯 Redis 路径，便于在同一进程内对比 Caffeine + Redis 与纯 Redis 的效果。</p>
 */
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

    /**
     * 从 L1 读取活动详情 JSON。
     *
     * <p>请求数只统计 L1 开启且 Key 类型受支持的访问；命中数只统计 Caffeine
     * 中实际存在条目的访问，因此 hitRate 可以直接反映活动详情本地缓存收益。</p>
     *
     * @param key Redis Key
     * @return 本地缓存值，未命中或 Key 不支持时返回 null
     */
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

    /**
     * 写入活动详情 JSON 到 L1。
     *
     * <p>调用方可以无差别传入 Redis Key，本方法会在内部过滤非活动详情 Key。</p>
     *
     * @param key Redis Key
     * @param value Redis 中同格式的 JSON 或空值占位
     */
    public void put(String key, String value) {
        if (isEnabled() && supports(key)) {
            cache.put(key, value);
        }
    }

    /**
     * 失效单个活动详情 L1 条目。
     *
     * @param key Redis Key
     */
    public void invalidate(String key) {
        if (supports(key)) {
            cache.invalidate(key);
        }
    }

    /**
     * 清空所有活动详情 L1 条目。
     */
    public void invalidateAll() {
        cache.invalidateAll();
        cache.cleanUp();
    }

    /**
     * 清空本地条目并重置命中率统计。
     *
     * <p>管理操作可调用该方法，避免上一轮缓存条目和统计计数污染本轮结果。</p>
     */
    public void resetStatsAndEntries() {
        invalidateAll();
        requestCount.reset();
        hitCount.reset();
    }

    /**
     * 设置运行时 L1 开关。
     *
     * <p>配置文件中的 enabled 是总开关；运行时开关只在总开关开启时生效。关闭运行时
     * 开关会立即清空 Caffeine，确保后续请求真实走 Redis。</p>
     *
     * @param enabled true 表示开启活动 L1，false 表示回退到纯 Redis
     */
    public void setRuntimeEnabled(boolean enabled) {
        runtimeEnabled.set(enabled);
        if (!enabled) {
            invalidateAll();
        }
        log.info("Activity local cache runtime enabled={}", isEnabled());
    }

    /**
     * 返回当前活动 L1 是否可用。
     *
     * @return true 表示配置总开关和运行时开关均开启
     */
    public boolean enabled() {
        return isEnabled();
    }

    /**
     * 返回活动详情 L1 命中率。
     *
     * @return hitCount / requestCount，无请求时返回 0
     */
    public double hitRate() {
        long requests = requestCount.sum();
        long hits = hitCount.sum();
        return requests == 0 ? 0.0 : (double) hits / requests;
    }

    /**
     * 返回进入 L1 查询路径的请求数。
     *
     * @return L1 支持 Key 的查询次数
     */
    public long requestCount() {
        return requestCount.sum();
    }

    /**
     * 返回当前 Caffeine 条目数估算值。
     *
     * @return 活动详情 L1 条目数
     */
    public long size() {
        return cache.estimatedSize();
    }

    /**
     * 判断配置总开关和运行时开关是否同时开启。
     *
     * @return true 表示可以访问 L1
     */
    private boolean isEnabled() {
        return properties.isEnabled() && runtimeEnabled.get();
    }

    /**
     * 判断 Key 是否允许进入活动详情 L1。
     *
     * <p>重建锁 Key 也以活动详情前缀开头，需要显式排除，避免锁值被误当成业务 JSON。</p>
     *
     * @param key Redis Key
     * @return true 表示该 Key 是活动详情或活动空值占位
     */
    private boolean supports(String key) {
        return key != null
                && key.startsWith(RedisKeys.SECKILL_ACTIVITY_PREFIX)
                && !key.startsWith(RedisKeys.SECKILL_ACTIVITY_PREFIX + "lock:");
    }
}
