package com.shanpu.common.annotation;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 标记需要进入滑动窗口限流的接口方法。
 *
 * <p>切面会按配置维度组装 Redis Key，并交给 Lua 脚本在 Redis 内原子完成
 * 窗口清理、计数和本次请求写入。</p>
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RateLimit {

    /**
     * 滑动窗口长度，单位为秒。
     *
     * @return 窗口秒数
     */
    int window() default 1;

    /**
     * 单个窗口内允许通过的最大请求数。
     *
     * @return 最大请求数
     */
    int max() default 10;

    /**
     * 限流统计维度。
     *
     * @return 用户、IP 或接口全局维度
     */
    Dimension dim() default Dimension.USER;

    /**
     * 自定义接口维度后缀。
     *
     * <p>为空时默认使用声明类名加方法名，保证同一维度下不同接口互不影响。</p>
     *
     * @return Redis Key 中的接口后缀
     */
    String key() default "";

    /**
     * 限流维度枚举。
     */
    enum Dimension {
        /**
         * 按当前登录用户限流，适用于下单、抢购等登录后操作。
         */
        USER,
        /**
         * 按客户端 IP 限流，适用于登录、注册等未登录接口。
         */
        IP,
        /**
         * 按接口全局限流，适用于需要整体保护的入口。
         */
        API
    }
}
