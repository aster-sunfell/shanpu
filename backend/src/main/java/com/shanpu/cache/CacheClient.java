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

/**
 * Redis 缓存访问模板。
 *
 * <p>本类统一封装业务读路径的缓存策略：空值占位用于防穿透，逻辑过期用于热点
 * Key 防击穿，互斥锁用于低 TTL 缓存重建。秒杀活动详情在 Redis 之上额外接入
 * {@link ActivityLocalCache}，形成 L1 Caffeine、L2 Redis、L3 MySQL 的读取链路。</p>
 *
 * <p>L1 只接受秒杀活动详情 Key。其他业务 Key 调用本类时仍会执行 put / invalidate，
 * 但 {@link ActivityLocalCache} 会按 Key 类型自动跳过，因此不会改变商品、订单等
 * 单层 Redis 缓存语义。</p>
 */
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

    /**
     * 写入普通 TTL 缓存。
     *
     * <p>Redis 写入成功后同步尝试回填 L1。只有活动详情 Key 会真正进入 Caffeine，
     * 其余 Key 会被本地缓存层忽略。</p>
     *
     * @param key Redis Key
     * @param value 业务对象
     * @param ttl Redis 物理过期时间
     */
    public void set(String key, Object value, Duration ttl) {
        try {
            String json = objectMapper.writeValueAsString(value);
            redisTemplate.opsForValue().set(key, json, ttl);
            activityLocalCache.put(key, json);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to serialize cache value for key " + key, e);
        }
    }

    /**
     * 写入逻辑过期缓存。
     *
     * <p>Redis Key 本身不设置物理 TTL，过期时间写在 {@link RedisData} 中。读到
     * 逻辑过期数据时先返回旧值，再由后台线程异步重建，避免热点活动详情在重建窗口
     * 内大量请求直接打到数据库。</p>
     *
     * @param key Redis Key
     * @param value 业务对象
     * @param ttl 逻辑有效期
     */
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

    /**
     * 删除单个缓存 Key，并同步失效对应 L1 条目。
     *
     * @param key Redis Key
     */
    public void delete(String key) {
        redisTemplate.delete(key);
        activityLocalCache.invalidate(key);
    }

    /**
     * 按模式扫描并删除 Redis Key。
     *
     * <p>活动详情批量删除时，先清空 L1，避免 Redis 已删除但 JVM 内仍保留短期旧值。
     * 商品列表等非活动 Key 不进入 L1，只按 Redis SCAN 分批删除。</p>
     *
     * @param pattern Redis SCAN match 表达式
     */
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

    /**
     * 通过 Key 前缀和 ID 查询普通 TTL 缓存。
     *
     * @param keyPrefix Redis Key 前缀
     * @param id 业务 ID
     * @param type 返回对象类型
     * @param dbFallback 数据库回源函数
     * @param ttl 正常值 TTL
     * @return 缓存或数据库中的业务对象，不存在时返回 null
     */
    public <R, ID> R queryWithCacheAside(String keyPrefix, ID id, Class<R> type,
                                         Function<ID, R> dbFallback,
                                         Duration ttl) {
        return queryWithCacheAside(keyPrefix + id, javaType(type), () -> dbFallback.apply(id), ttl);
    }

    /**
     * 查询普通 TTL 缓存，未命中时回源并只缓存真实值。
     *
     * <p>该方法只解决 cache-aside 读写，不承担空值缓存职责。</p>
     *
     * @param key Redis Key
     * @param type 返回对象类型
     * @param dbFallback 数据库回源函数
     * @param ttl 正常值 TTL
     * @return 缓存或数据库中的业务对象，不存在时返回 null
     */
    public <R> R queryWithCacheAside(String key, JavaType type, Supplier<R> dbFallback,
                                     Duration ttl) {
        String json = getCacheValue(key);
        if (json != null) {
            return read(json, type);
        }

        R value = dbFallback.get();
        if (value != null) {
            set(key, value, ttl);
        }
        return value;
    }

    /**
     * 用独立空值 Key 包裹任意读取逻辑，专门拦截稳定不存在资源的重复访问。
     *
     * @param nullKey 独立空值占位 Key
     * @param valueSupplier 真实值加载逻辑，可组合 cache-aside、逻辑过期或直接 DB 查询
     * @param nullTtl 空值占位 TTL
     * @return 真实业务对象，不存在时返回 null
     */
    public <R> R queryWithNullCache(String nullKey, Supplier<R> valueSupplier, Duration nullTtl) {
        if (hasNullPlaceholder(nullKey)) {
            return null;
        }

        R value = valueSupplier.get();
        if (value == null) {
            redisTemplate.opsForValue().set(nullKey, EMPTY_VALUE, nullTtl);
            activityLocalCache.put(nullKey, EMPTY_VALUE);
        }
        return value;
    }

    /**
     * 通过逻辑过期策略查询缓存。
     *
     * @param keyPrefix Redis Key 前缀
     * @param id 业务 ID
     * @param type 返回对象类型
     * @param dbFallback 数据库回源函数
     * @param ttl 逻辑有效期
     * @return 缓存或数据库中的业务对象，不存在时返回 null
     */
    public <R, ID> R queryWithLogicalExpire(String keyPrefix, ID id, Class<R> type,
                                            Function<ID, R> dbFallback,
                                            Duration ttl) {
        return queryWithLogicalExpire(keyPrefix + id, javaType(type), () -> dbFallback.apply(id),
                keyPrefix + "lock:" + id, ttl);
    }

    /**
     * 通过指定完整 Key 和锁 Key 查询逻辑过期缓存。
     *
     * @param key Redis Key
     * @param type 返回对象类型
     * @param dbFallback 数据库回源函数
     * @param lockKey 缓存重建锁 Key
     * @param ttl 逻辑有效期
     * @return 缓存或数据库中的业务对象，不存在时返回 null
     */
    public <R> R queryWithLogicalExpire(String key, JavaType type, Supplier<R> dbFallback,
                                        String lockKey, Duration ttl) {
        String json = getCacheValue(key);
        if (json == null) {
            // L1 和 L2 都未命中时同步回源，保证冷启动请求能拿到最新数据库数据。
            R value = dbFallback.get();
            if (value == null) {
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
            // 只让一个线程承担重建任务；当前请求继续返回旧值，避免等待 DB。
            CACHE_REBUILD_EXECUTOR.submit(() -> {
                try {
                    R latest = dbFallback.get();
                    if (latest != null) {
                        setWithLogicalExpire(key, latest, ttl);
                    } else {
                        delete(key);
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

    /**
     * 通过互斥锁策略查询缓存。
     *
     * @param keyPrefix Redis Key 前缀
     * @param id 业务 ID
     * @param type 返回对象类型
     * @param dbFallback 数据库回源函数
     * @param ttl 正常值 TTL
     * @return 缓存或数据库中的业务对象，不存在时返回 null
     */
    public <R, ID> R queryWithMutex(String keyPrefix, ID id, Class<R> type,
                                    Function<ID, R> dbFallback,
                                    Duration ttl) {
        return queryWithMutex(keyPrefix + id, javaType(type), () -> dbFallback.apply(id),
                keyPrefix + "lock:" + id, ttl);
    }

    /**
     * 查询缓存，未命中时用 Redis 锁串行化数据库回源。
     *
     * <p>该策略适合订单详情等低 TTL 场景。未拿到锁的线程短暂等待并二次读取 Redis，
     * 避免同一订单详情在轮询窗口内被多个请求同时重建。</p>
     *
     * @param key Redis Key
     * @param type 返回对象类型
     * @param dbFallback 数据库回源函数
     * @param lockKey 缓存重建锁 Key
     * @param ttl 正常值 TTL
     * @return 缓存或数据库中的业务对象，不存在时返回 null
     */
    public <R> R queryWithMutex(String key, JavaType type, Supplier<R> dbFallback,
                                String lockKey, Duration ttl) {
        String json = getCacheValue(key);
        if (json != null) {
            return read(json, type);
        }

        boolean locked = false;
        try {
            for (int i = 0; i < 3; i++) {
                locked = tryLock(lockKey);
                if (locked) {
                    // 拿锁后再查一次 Redis，避免前一个重建线程已经写回缓存。
                    String doubleCheck = getCacheValue(key);
                    if (doubleCheck != null) {
                        return read(doubleCheck, type);
                    }
                    R value = dbFallback.get();
                    if (value == null) {
                        return null;
                    }
                    set(key, value, ttl);
                    return value;
                }
                TimeUnit.MILLISECONDS.sleep(50);
                // 未拿到锁时短暂等待，优先复用其他线程刚写回的缓存。
                json = getCacheValue(key);
                if (json != null) {
                    return read(json, type);
                }
            }
            // 多次等待后仍无缓存，直接回源，保证接口不会因锁竞争无限等待。
            R value = dbFallback.get();
            if (value == null) {
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

    /**
     * 构造 Jackson 反序列化类型。
     *
     * @param rawClass 原始类型
     * @param parameterClasses 泛型参数类型
     * @return Jackson JavaType
     */
    public JavaType javaType(Class<?> rawClass, Class<?>... parameterClasses) {
        if (parameterClasses.length == 0) {
            return objectMapper.getTypeFactory().constructType(rawClass);
        }
        return objectMapper.getTypeFactory().constructParametricType(rawClass, parameterClasses);
    }

    /**
     * 获取缓存重建锁。
     *
     * @param key 锁 Key
     * @return true 表示当前线程拿到重建资格
     */
    private boolean tryLock(String key) {
        Boolean ok = redisTemplate.opsForValue().setIfAbsent(key, "1", LOCK_TTL);
        return Boolean.TRUE.equals(ok);
    }

    /**
     * 释放缓存重建锁。
     *
     * @param key 锁 Key
     */
    private void unlock(String key) {
        redisTemplate.delete(key);
    }

    /**
     * 按 L1、L2 顺序读取缓存值。
     *
     * <p>Redis 命中后立即尝试回填 L1，使同一 JVM 内后续活动详情请求可以直接命中
     * Caffeine。非活动 Key 会被 {@link ActivityLocalCache#put(String, String)} 自动忽略。</p>
     *
     * @param key Redis Key
     * @return 缓存 JSON，未命中时返回 null
     */
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

    /**
     * 判断独立空值占位 Key 是否命中。
     */
    private boolean hasNullPlaceholder(String nullKey) {
        String value = getCacheValue(nullKey);
        if (value == null) {
            return false;
        }
        if (value.isEmpty()) {
            return true;
        }
        delete(nullKey);
        return false;
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
