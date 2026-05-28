package com.shanpu.common.util;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class UserContextTest {

    @AfterEach
    void tearDown() {
        UserContext.remove();
    }

    @Test
    void setAndGet_returnsValue() {
        UserContext.set(42L);
        assertEquals(42L, UserContext.get());
    }

    @Test
    void remove_clearsThreadLocal() {
        UserContext.set(42L);
        UserContext.remove();
        assertNull(UserContext.get());
    }

    @Test
    void requireUserId_throwsWhenEmpty() {
        BusinessException ex = assertThrows(BusinessException.class, UserContext::requireUserId);
        assertEquals(ResultCode.UNAUTHORIZED, ex.getResultCode());
    }
}
