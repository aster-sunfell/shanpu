package com.shanpu.common.util;

import io.jsonwebtoken.Claims;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JwtUtilTest {

    private static final String SECRET = "replace-me-with-a-32-byte-random-secret-for-dev-only";
    private JwtUtil jwtUtil;

    @BeforeEach
    void setUp() {
        jwtUtil = new JwtUtil(SECRET, 7);
    }

    @Test
    void signThenParse_returnsSameUidAndUname() {
        String token = jwtUtil.sign(42L, "alice");
        Claims claims = jwtUtil.parse(token);
        assertEquals(42, ((Number) claims.get("uid")).longValue());
        assertEquals("alice", claims.get("uname"));
    }

    @Test
    void parseExpiredToken_throws() {
        JwtUtil shortLived = new JwtUtil(SECRET, 0);
        String token = shortLived.sign(1L, "bob");
        assertThrows(Exception.class, () -> shortLived.parse(token));
    }

    @Test
    void tokenHash_isDeterministic32HexChars() {
        String token = jwtUtil.sign(1L, "test");
        String hash1 = JwtUtil.tokenHash(token);
        String hash2 = JwtUtil.tokenHash(token);
        assertEquals(hash1, hash2);
        assertEquals(32, hash1.length());
        assertTrue(hash1.matches("^[0-9a-f]{32}$"));
    }
}
