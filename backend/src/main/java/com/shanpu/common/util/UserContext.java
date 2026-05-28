package com.shanpu.common.util;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;

public final class UserContext {

    private static final ThreadLocal<Long> HOLDER = new ThreadLocal<>();

    private UserContext() {}

    public static void set(Long userId) {
        HOLDER.set(userId);
    }

    public static Long get() {
        return HOLDER.get();
    }

    public static Long requireUserId() {
        Long uid = HOLDER.get();
        if (uid == null) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }
        return uid;
    }

    public static void remove() {
        HOLDER.remove();
    }
}
