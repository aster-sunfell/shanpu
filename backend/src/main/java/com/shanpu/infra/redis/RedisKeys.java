package com.shanpu.infra.redis;

public final class RedisKeys {

    private RedisKeys() {}

    public static final String USER_SESSION_PREFIX = "shanpu:user:session:";

    public static String userSession(String tokenHash) {
        return USER_SESSION_PREFIX + tokenHash;
    }
}
