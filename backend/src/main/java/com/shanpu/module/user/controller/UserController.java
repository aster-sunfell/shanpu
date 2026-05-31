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

    @RateLimit(window = 60, max = 3, dim = RateLimit.Dimension.IP)
    @PostMapping("/register")
    public Result<RegisterVO> register(@Valid @RequestBody RegisterRequest req) {
        return Result.ok(userService.register(req));
    }

    @RateLimit(window = 60, max = 5, dim = RateLimit.Dimension.IP)
    @PostMapping("/login")
    public Result<LoginVO> login(@Valid @RequestBody LoginRequest req) {
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
