package com.shanpu.common.result;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class ResultTest {

    @Test
    void okWithData_hasCode0() {
        Result<String> r = Result.ok("hello");
        assertEquals(0, r.getCode());
        assertEquals("ok", r.getMsg());
        assertEquals("hello", r.getData());
    }

    @Test
    void okVoid_hasCode0NullData() {
        Result<Void> r = Result.ok();
        assertEquals(0, r.getCode());
        assertNull(r.getData());
    }

    @Test
    void failWithResultCode_copiesCodeAndMsg() {
        Result<Void> r = Result.fail(ResultCode.UNAUTHORIZED);
        assertEquals(40100, r.getCode());
        assertEquals(ResultCode.UNAUTHORIZED.getMsg(), r.getMsg());
        assertNull(r.getData());
    }

    @Test
    void failWithCodeAndMsg_setsValues() {
        Result<Void> r = Result.fail(99999, "custom");
        assertEquals(99999, r.getCode());
        assertEquals("custom", r.getMsg());
        assertNull(r.getData());
    }
}
