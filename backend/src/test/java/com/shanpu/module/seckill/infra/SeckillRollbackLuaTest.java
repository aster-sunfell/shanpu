package com.shanpu.module.seckill.infra;

import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.assertTrue;

class SeckillRollbackLuaTest {

    @Test
    void rollbackScriptIncrementsStockOnlyWhenBoughtMemberRemoved() throws Exception {
        ClassPathResource resource = new ClassPathResource("lua/seckill-rollback.lua");
        String lua = resource.getContentAsString(StandardCharsets.UTF_8);

        assertTrue(lua.contains("local removed = redis.call('SREM', KEYS[2], ARGV[1])"));
        assertTrue(lua.contains("if removed == 1 then"));
        assertTrue(lua.contains("redis.call('INCR', KEYS[1])"));
        assertTrue(lua.contains("return removed"));
    }
}
