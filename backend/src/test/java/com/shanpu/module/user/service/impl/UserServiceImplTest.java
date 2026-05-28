package com.shanpu.module.user.service.impl;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.common.result.ResultCode;
import com.shanpu.common.util.JwtUtil;
import com.shanpu.common.util.UserContext;
import com.shanpu.infra.redis.RedisKeys;
import com.shanpu.module.user.domain.dto.LoginRequest;
import com.shanpu.module.user.domain.dto.RegisterRequest;
import com.shanpu.module.user.domain.dto.UpdateNicknameRequest;
import com.shanpu.module.user.domain.entity.PointLog;
import com.shanpu.module.user.domain.entity.User;
import com.shanpu.module.user.domain.vo.LoginVO;
import com.shanpu.module.user.domain.vo.PointsVO;
import com.shanpu.module.user.domain.vo.RegisterVO;
import com.shanpu.module.user.domain.vo.UserVO;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Duration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class UserServiceImplTest {

    @Mock
    private UserMapper userMapper;

    @Mock
    private PointLogMapper pointLogMapper;

    @Mock
    private JwtUtil jwtUtil;

    @Mock
    private StringRedisTemplate redisTemplate;

    @Mock
    private ValueOperations<String, String> valueOperations;

    @InjectMocks
    private UserServiceImpl userService;

    @AfterEach
    void tearDown() {
        UserContext.remove();
    }

    @Test
    void register_duplicateUsername_throws() {
        RegisterRequest req = new RegisterRequest();
        req.setUsername("alice");
        req.setPassword("pwd123456");
        when(userMapper.selectByUsername("alice")).thenReturn(new User());

        assertThrows(BusinessException.class, () -> userService.register(req));
    }

    @Test
    void register_persistsUserWith10000PointsAndLog() {
        RegisterRequest req = new RegisterRequest();
        req.setUsername("newuser");
        req.setPassword("pwd123456");
        req.setNickname("Nick");

        when(userMapper.selectByUsername("newuser")).thenReturn(null);
        when(userMapper.insert(any(User.class))).thenAnswer(inv -> {
            User u = inv.getArgument(0);
            u.setId(1L);
            return 1;
        });
        when(pointLogMapper.insert(any(PointLog.class))).thenReturn(1);

        RegisterVO vo = userService.register(req);

        assertEquals(1L, vo.getUserId());
        assertEquals("newuser", vo.getUsername());
        assertEquals(10000, vo.getPoints());

        ArgumentCaptor<User> userCaptor = ArgumentCaptor.forClass(User.class);
        verify(userMapper).insert(userCaptor.capture());
        assertTrue(userCaptor.getValue().getPassword().startsWith("$2a$"));

        ArgumentCaptor<PointLog> logCaptor = ArgumentCaptor.forClass(PointLog.class);
        verify(pointLogMapper).insert(logCaptor.capture());
        PointLog log = logCaptor.getValue();
        assertEquals(10000, log.getDelta());
        assertEquals(10000, log.getBalanceAfter());
        assertEquals(PointLog.BIZ_REGISTER_BONUS, log.getBizType());
    }

    @Test
    void login_wrongPassword_throws() {
        LoginRequest req = new LoginRequest();
        req.setUsername("alice");
        req.setPassword("wrong");

        User user = new User();
        user.setPassword("$2a$10$invalidhash");
        when(userMapper.selectByUsername("alice")).thenReturn(user);

        assertThrows(BusinessException.class, () -> userService.login(req));
    }

    @Test
    void login_unknownUser_throws() {
        LoginRequest req = new LoginRequest();
        req.setUsername("nobody");
        req.setPassword("pwd");
        when(userMapper.selectByUsername("nobody")).thenReturn(null);

        assertThrows(BusinessException.class, () -> userService.login(req));
    }

    @Test
    void login_success_signsTokenWritesRedis() {
        LoginRequest req = new LoginRequest();
        req.setUsername("alice");
        req.setPassword("pwd123456");

        User user = new User();
        user.setId(1L);
        user.setUsername("alice");
        user.setPoints(10000);
        user.setPassword("$2a$10$MSIpkHyqmDpPonfUdGiFJ..c1BX1nsiwAI0gV4F8pBQ7FLyKkK9s6");
        when(userMapper.selectByUsername("alice")).thenReturn(user);
        when(jwtUtil.sign(1L, "alice")).thenReturn("mock-token");
        when(jwtUtil.getExpireMs()).thenReturn(Duration.ofDays(7).toMillis());
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);

        LoginVO vo = userService.login(req);

        assertEquals("mock-token", vo.getToken());
        assertEquals(1L, vo.getUserId());
        assertEquals("alice", vo.getUsername());
        assertEquals(10000, vo.getPoints());

        verify(valueOperations).set(anyString(), eq("1"), any(Duration.class));
    }

    @Test
    void logout_blankToken_isNoOp() {
        userService.logout("");
        // no exception thrown, no Redis interaction
    }

    @Test
    void logout_deletesSessionKey() {
        when(redisTemplate.delete(anyString())).thenReturn(true);
        userService.logout("some-token");
        String expectedHash = JwtUtil.tokenHash("some-token");
        verify(redisTemplate).delete(RedisKeys.userSession(expectedHash));
    }

    @Test
    void getCurrentUser_returnsUserVO() {
        UserContext.set(1L);
        User user = new User();
        user.setId(1L);
        user.setUsername("alice");
        user.setNickname("Alice");
        user.setPoints(10000);
        when(userMapper.selectById(1L)).thenReturn(user);

        UserVO vo = userService.getCurrentUser();

        assertEquals(1L, vo.getUserId());
        assertEquals("alice", vo.getUsername());
        assertEquals("Alice", vo.getNickname());
        assertEquals(10000, vo.getPoints());
    }

    @Test
    void getCurrentUser_throwsWhenUserMissing() {
        UserContext.set(999L);
        when(userMapper.selectById(999L)).thenReturn(null);
        assertThrows(BusinessException.class, () -> userService.getCurrentUser());
    }

    @Test
    void getPoints_returnsValue() {
        UserContext.set(1L);
        when(userMapper.selectPointsById(1L)).thenReturn(5000);

        PointsVO vo = userService.getPoints();
        assertEquals(5000, vo.getPoints());
    }

    @Test
    void getPoints_throwsWhenUserMissing() {
        UserContext.set(999L);
        when(userMapper.selectPointsById(999L)).thenReturn(null);
        assertThrows(BusinessException.class, () -> userService.getPoints());
    }

    @Test
    void updateNickname_updatesAndReturnsUserVO() {
        UserContext.set(1L);
        UpdateNicknameRequest req = new UpdateNicknameRequest();
        req.setNickname("  Alice  ");
        when(userMapper.updateNickname(1L, "Alice")).thenReturn(1);

        User user = new User();
        user.setId(1L);
        user.setUsername("alice");
        user.setNickname("Alice");
        user.setPoints(10000);
        when(userMapper.selectById(1L)).thenReturn(user);

        UserVO vo = userService.updateNickname(req);

        assertEquals("Alice", vo.getNickname());
        assertEquals(10000, vo.getPoints());
    }

    @Test
    void updateNickname_clearsBlankNickname() {
        UserContext.set(1L);
        UpdateNicknameRequest req = new UpdateNicknameRequest();
        req.setNickname("   ");
        when(userMapper.updateNickname(1L, "")).thenReturn(1);

        User user = new User();
        user.setId(1L);
        user.setUsername("alice");
        user.setNickname("");
        user.setPoints(10000);
        when(userMapper.selectById(1L)).thenReturn(user);

        UserVO vo = userService.updateNickname(req);

        assertEquals("", vo.getNickname());
    }

    @Test
    void updateNickname_throwsWhenUserMissing() {
        UserContext.set(999L);
        UpdateNicknameRequest req = new UpdateNicknameRequest();
        req.setNickname("Ghost");
        when(userMapper.updateNickname(999L, "Ghost")).thenReturn(0);

        assertThrows(BusinessException.class, () -> userService.updateNickname(req));
    }
}
