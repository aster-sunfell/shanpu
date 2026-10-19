package com.shanpu.cache;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;

/**
 * 逻辑过期缓存的 Redis 载荷。
 *
 * <p>Redis Key 本身可以不设置物理过期时间，业务有效期写入 {@link #expireTime}。
 * 读请求发现逻辑过期后仍先返回 {@link #data} 中的旧值，再由后台线程异步回源重建，
 * 避免热点商品或活动详情在缓存重建期间击穿到数据库。</p>
 */
@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class RedisData {

    /**
     * 业务数据的逻辑过期时间。
     */
    private LocalDateTime expireTime;

    /**
     * 被缓存的业务对象。
     */
    private Object data;
}
