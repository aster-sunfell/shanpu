package com.shanpu.module.user.domain.dto;

import jakarta.validation.constraints.Size;
import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class UpdateNicknameRequest {

    @Size(max = 32, message = "昵称最长32字符")
    private String nickname;
}
