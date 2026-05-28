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
import com.shanpu.module.user.service.UserService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;

@Slf4j
@Service
@RequiredArgsConstructor
public class UserServiceImpl implements UserService {

    private final UserMapper userMapper;
    private final PointLogMapper pointLogMapper;
    private final JwtUtil jwtUtil;
    private final StringRedisTemplate redisTemplate;

    private static final int REGISTER_BONUS = 10000;
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    @Override
    @Transactional(rollbackFor = Exception.class)
    public RegisterVO register(RegisterRequest req) {
        if (userMapper.selectByUsername(req.getUsername()) != null) {
            throw new BusinessException(ResultCode.PARAM_INVALID, "用户名已存在");
        }

        User user = new User();
        user.setUsername(req.getUsername());
        user.setPassword(passwordEncoder.encode(req.getPassword()));
        user.setNickname(req.getNickname() == null ? "" : req.getNickname());
        user.setPoints(REGISTER_BONUS);
        userMapper.insert(user);

        PointLog pointLog = new PointLog();
        pointLog.setUserId(user.getId());
        pointLog.setDelta(REGISTER_BONUS);
        pointLog.setBalanceAfter(REGISTER_BONUS);
        pointLog.setBizType(PointLog.BIZ_REGISTER_BONUS);
        pointLog.setBizRef("");
        pointLogMapper.insert(pointLog);

        return RegisterVO.builder()
                .userId(user.getId())
                .username(user.getUsername())
                .points(REGISTER_BONUS)
                .build();
    }

    @Override
    public LoginVO login(LoginRequest req) {
        User user = userMapper.selectByUsername(req.getUsername());
        if (user == null || !passwordEncoder.matches(req.getPassword(), user.getPassword())) {
            throw new BusinessException(ResultCode.PARAM_INVALID, "用户名或密码错误");
        }

        String token = jwtUtil.sign(user.getId(), user.getUsername());
        String tokenHash = JwtUtil.tokenHash(token);
        redisTemplate.opsForValue().set(
                RedisKeys.userSession(tokenHash),
                String.valueOf(user.getId()),
                Duration.ofMillis(jwtUtil.getExpireMs())
        );

        return LoginVO.builder()
                .token(token)
                .userId(user.getId())
                .username(user.getUsername())
                .points(user.getPoints())
                .build();
    }

    @Override
    public void logout(String token) {
        if (token == null || token.isBlank()) {
            return;
        }
        String tokenHash = JwtUtil.tokenHash(token);
        redisTemplate.delete(RedisKeys.userSession(tokenHash));
    }

    @Override
    public UserVO getCurrentUser() {
        Long uid = UserContext.requireUserId();
        User user = userMapper.selectById(uid);
        if (user == null) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }
        return UserVO.builder()
                .userId(user.getId())
                .username(user.getUsername())
                .nickname(user.getNickname())
                .points(user.getPoints())
                .build();
    }

    @Override
    public PointsVO getPoints() {
        Long uid = UserContext.requireUserId();
        Integer points = userMapper.selectPointsById(uid);
        if (points == null) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }
        return PointsVO.builder().points(points).build();
    }

    @Override
    public UserVO updateNickname(UpdateNicknameRequest req) {
        Long uid = UserContext.requireUserId();
        String nickname = req.getNickname() == null ? "" : req.getNickname().trim();
        if (userMapper.updateNickname(uid, nickname) == 0) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }
        User user = userMapper.selectById(uid);
        if (user == null) {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }
        return UserVO.builder()
                .userId(user.getId())
                .username(user.getUsername())
                .nickname(user.getNickname())
                .points(user.getPoints())
                .build();
    }
}
