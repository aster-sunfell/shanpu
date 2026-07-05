package com.shanpu.infra.redis;

public final class RedisKeys {

    private RedisKeys() {}

    public static final String USER_SESSION_PREFIX = "shanpu:user:session:";
    public static final String PRODUCT_DETAIL_PREFIX = "shanpu:product:detail:";
    public static final String PRODUCT_LIST_PREFIX = "shanpu:product:list:";
    public static final String SECKILL_ACTIVITY_PREFIX = "shanpu:seckill:activity:";
    public static final String SECKILL_STOCK_PREFIX = "shanpu:seckill:stock:";
    public static final String SECKILL_BOUGHT_PREFIX = "shanpu:seckill:bought:";
    public static final String ORDER_DETAIL_PREFIX = "shanpu:order:detail:";
    public static final String RATE_LIMIT_PREFIX = "shanpu:rate:";

    public static String userSession(String tokenHash) {
        return USER_SESSION_PREFIX + tokenHash;
    }

    public static String productList(int page, int size) {
        return PRODUCT_LIST_PREFIX + "page:" + page + ":size:" + size;
    }

    public static String productListPattern() {
        return PRODUCT_LIST_PREFIX + "*";
    }

    public static String productDetail(Long productId) {
        return PRODUCT_DETAIL_PREFIX + productId;
    }

    public static String productDetailNull(Long productId) {
        return PRODUCT_DETAIL_PREFIX + "null:" + productId;
    }

    public static String seckillActivity(Long activityId) {
        return SECKILL_ACTIVITY_PREFIX + activityId;
    }

    public static String seckillActivityNull(Long activityId) {
        return SECKILL_ACTIVITY_PREFIX + "null:" + activityId;
    }

    public static String seckillActivityLock(Long activityId) {
        return SECKILL_ACTIVITY_PREFIX + "lock:" + activityId;
    }

    public static String seckillStock(Long activityId) {
        return SECKILL_STOCK_PREFIX + activityId;
    }

    public static String seckillBought(Long activityId) {
        return SECKILL_BOUGHT_PREFIX + activityId;
    }

    public static String orderDetail(Long userId, String orderId) {
        return ORDER_DETAIL_PREFIX + "user:" + userId + ":" + orderId;
    }

    public static String orderDetailLock(Long userId, String orderId) {
        return ORDER_DETAIL_PREFIX + "lock:user:" + userId + ":" + orderId;
    }

    /**
     * 生成滑动窗口限流 Redis Key。
     *
     * @param dimension 限流维度和值，例如 {@code user:1}、{@code ip:127.0.0.1} 或 {@code api}
     * @param apiKey 接口标识，默认是声明类名加方法名
     * @return 形如 {@code shanpu:rate:{dimension}:{apiKey}} 的限流 Key
     */
    public static String rateLimit(String dimension, String apiKey) {
        return RATE_LIMIT_PREFIX + dimension + ":" + apiKey;
    }
}
