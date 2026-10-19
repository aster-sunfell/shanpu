package com.shanpu.cache;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 秒杀活动 L1 本地缓存配置。
 *
 * <p>配置项对应 {@code shanpu.cache.local.activity.*}。默认只给活动详情一个很短的
 * Caffeine 缓存窗口，用于吸收同一 JVM 内的热点读流量，同时保留 Redis 作为跨进程共享的
 * L2 缓存。</p>
 */
@Getter
@Setter
@Component
@ConfigurationProperties(prefix = "shanpu.cache.local.activity")
public class ActivityLocalCacheProperties {

    /**
     * 配置总开关。
     *
     * <p>false 时运行时开关即使打开也不会启用 L1。</p>
     */
    private boolean enabled = true;

    /**
     * Caffeine 最大条目数。
     *
     * <p>按同时活跃的秒杀活动数量预留容量，避免本地缓存无界增长。</p>
     */
    private long maxSize = 256;

    /**
     * 写入后过期时间，单位毫秒。
     *
     * <p>活动详情使用短 TTL，允许 Redis 或数据库中的活动状态变更在秒级内自然收敛。</p>
     */
    private long expireMs = 1000;
}
