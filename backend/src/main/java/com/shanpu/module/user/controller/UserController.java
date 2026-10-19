package com.shanpu.module.user.controller;

import com.shanpu.common.annotation.RateLimit;
import com.shanpu.common.result.Result;
import com.shanpu.module.user.domain.dto.LoginRequest;
import com.shanpu.module.user.domain.dto.RegisterRequest;
import com.shanpu.module.user.domain.dto.UpdateNicknameRequest;
import com.shanpu.module.user.domain.vo.LoginVO;
import com.shanpu.module.user.domain.vo.PointsVO;
import com.shanpu.module.user.domain.vo.RegisterVO;
import com.shanpu.module.user.domain.vo.UserVO;
import com.shanpu.module.user.service.UserService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/users")
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    /**
     * 注册新用户。
     *
     * <p>注册接口按 IP 做分钟级滑动窗口限流，避免匿名流量批量创建账号。</p>
     *
     * @param req 注册请求
     * @return 注册后的用户信息与初始积分
     */
    @RateLimit(window = 60, max = 3, dim = RateLimit.Dimension.IP)
    @PostMapping("/register")
    public Result<RegisterVO> register(@Valid @RequestBody RegisterRequest req) {
        // 限流切面放行后再进入注册业务，注册成功会初始化用户积分。
        return Result.ok(userService.register(req));
    }

    /**
     * 用户登录并签发访问令牌。
     *
     * <p>登录接口按 IP 做分钟级滑动窗口限流，降低密码爆破和 BCrypt 计算压力。</p>
     *
     * @param req 登录请求
     * @return JWT 与登录用户信息
     */
    @RateLimit(window = 60, max = 5, dim = RateLimit.Dimension.IP)
    @PostMapping("/login")
    public Result<LoginVO> login(@Valid @RequestBody LoginRequest req) {
        // 限流切面先拦截高频登录尝试，服务层只处理允许进入的密码校验请求。
        return Result.ok(userService.login(req));
    }

    @PostMapping("/logout")
    public Result<Void> logout(HttpServletRequest request) {
        String header = request.getHeader("Authorization");
        if (header != null && header.startsWith("Bearer ")) {
            userService.logout(header.substring(7));
        }
        return Result.ok();
    }

    @GetMapping("/me")
    public Result<UserVO> me() {
        return Result.ok(userService.getCurrentUser());
    }

    @GetMapping("/me/points")
    public Result<PointsVO> myPoints() {
        return Result.ok(userService.getPoints());
    }

    @PostMapping("/me/nickname")
    public Result<UserVO> updateNickname(@Valid @RequestBody UpdateNicknameRequest req) {
        return Result.ok(userService.updateNickname(req));
    }
}
