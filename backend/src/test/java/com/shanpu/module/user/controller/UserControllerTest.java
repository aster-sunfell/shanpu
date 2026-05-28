package com.shanpu.module.user.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shanpu.common.result.ResultCode;
import com.shanpu.module.user.domain.dto.LoginRequest;
import com.shanpu.module.user.domain.dto.RegisterRequest;
import com.shanpu.module.user.domain.dto.UpdateNicknameRequest;
import com.shanpu.module.user.domain.vo.LoginVO;
import com.shanpu.module.user.domain.vo.PointsVO;
import com.shanpu.module.user.domain.vo.RegisterVO;
import com.shanpu.module.user.domain.vo.UserVO;
import com.shanpu.module.user.service.UserService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(MockitoExtension.class)
class UserControllerTest {

    private MockMvc mockMvc;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final UserService userService = mock(UserService.class);

    @BeforeEach
    void setUp() {
        UserController controller = new UserController(userService);
        mockMvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    @Test
    void register_returnsResult() throws Exception {
        RegisterVO vo = RegisterVO.builder().userId(1L).username("alice").points(10000).build();
        when(userService.register(any(RegisterRequest.class))).thenReturn(vo);

        mockMvc.perform(post("/api/users/register")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new RegisterRequest() {{ setUsername("alice"); setPassword("pwd123456"); }})))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.userId").value(1))
                .andExpect(jsonPath("$.data.points").value(10000));
    }

    @Test
    void login_returnsResult() throws Exception {
        LoginVO vo = LoginVO.builder().token("jwt-token").userId(1L).username("alice").points(10000).build();
        when(userService.login(any(LoginRequest.class))).thenReturn(vo);

        mockMvc.perform(post("/api/users/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new LoginRequest() {{ setUsername("alice"); setPassword("pwd123456"); }})))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.token").value("jwt-token"));
    }

    @Test
    void me_returnsUserVO() throws Exception {
        UserVO vo = UserVO.builder().userId(1L).username("alice").nickname("A").points(10000).build();
        when(userService.getCurrentUser()).thenReturn(vo);

        mockMvc.perform(get("/api/users/me"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.username").value("alice"));
    }

    @Test
    void points_returnsPointsVO() throws Exception {
        PointsVO vo = PointsVO.builder().points(5000).build();
        when(userService.getPoints()).thenReturn(vo);

        mockMvc.perform(get("/api/users/me/points"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.points").value(5000));
    }

    @Test
    void updateNickname_returnsUserVO() throws Exception {
        UserVO vo = UserVO.builder().userId(1L).username("alice").nickname("Alice").points(10000).build();
        when(userService.updateNickname(any(UpdateNicknameRequest.class))).thenReturn(vo);

        UpdateNicknameRequest req = new UpdateNicknameRequest();
        req.setNickname("Alice");

        mockMvc.perform(post("/api/users/me/nickname")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(req)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.nickname").value("Alice"))
                .andExpect(jsonPath("$.data.points").value(10000));
    }
}
