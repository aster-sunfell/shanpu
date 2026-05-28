package com.shanpu.module.user.service;

import com.shanpu.module.user.domain.dto.LoginRequest;
import com.shanpu.module.user.domain.dto.RegisterRequest;
import com.shanpu.module.user.domain.dto.UpdateNicknameRequest;
import com.shanpu.module.user.domain.vo.LoginVO;
import com.shanpu.module.user.domain.vo.PointsVO;
import com.shanpu.module.user.domain.vo.RegisterVO;
import com.shanpu.module.user.domain.vo.UserVO;

public interface UserService {

    RegisterVO register(RegisterRequest req);

    LoginVO login(LoginRequest req);

    void logout(String token);

    UserVO getCurrentUser();

    PointsVO getPoints();

    UserVO updateNickname(UpdateNicknameRequest req);
}
