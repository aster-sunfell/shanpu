package com.shanpu.infra.redis;

public final class RedisKeys {

    private RedisKeys() {}

    public static final String USER_SESSION_PREFIX = "shanpu:user:session:";
    public static final String PRODUCT_DETAIL_PREFIX = "shanpu:product:detail:";
    public static final String PRODUCT_LIST_PREFIX = "shanpu:product:list:";
    public static final String ORDER_DETAIL_PREFIX = "shanpu:order:detail:";
    public static final String RATE_LIMIT_PREFIX = "shanpu:rate:";

    public static String userSession(String tokenHash) {
        return USER_SESSION_PREFIX + tokenHash;
    }

    public static String productList(int page, int size) {
        return PRODUCT_LIST_PREFIX + "page:" + page + ":size:" + size;
    }

    public static String orderDetail(Long userId, String orderId) {
        return ORDER_DETAIL_PREFIX + "user:" + userId + ":" + orderId;
    }

    public static String orderDetailLock(Long userId, String orderId) {
        return ORDER_DETAIL_PREFIX + "lock:user:" + userId + ":" + orderId;
    }

    public static String rateLimit(String dimension, String apiKey) {
        return RATE_LIMIT_PREFIX + dimension + ":" + apiKey;
    }
}
