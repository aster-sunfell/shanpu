package com.shanpu.common.exception;

import com.shanpu.common.result.ResultCode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class GlobalExceptionHandlerTest {

    private MockMvc mockMvc;

    @RestController
    static class TestController {
        @GetMapping("/test/unauthorized")
        public void unauthorized() {
            throw new BusinessException(ResultCode.UNAUTHORIZED);
        }

        @GetMapping("/test/rate-limited")
        public void rateLimited() {
            throw new BusinessException(ResultCode.RATE_LIMITED);
        }

        @GetMapping("/test/stock-shortage")
        public void stockShortage() {
            throw new BusinessException(ResultCode.STOCK_SHORTAGE);
        }

        @GetMapping("/test/generic")
        public void generic() {
            throw new RuntimeException("boom");
        }
    }

    @BeforeEach
    void setUp() {
        mockMvc = MockMvcBuilders
                .standaloneSetup(new TestController())
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void unauthorized_returns401() throws Exception {
        mockMvc.perform(get("/test/unauthorized"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void rateLimited_returns429() throws Exception {
        mockMvc.perform(get("/test/rate-limited"))
                .andExpect(status().isTooManyRequests());
    }

    @Test
    void stockShortage_returns200() throws Exception {
        mockMvc.perform(get("/test/stock-shortage"))
                .andExpect(status().isOk());
    }

    @Test
    void genericException_returns500() throws Exception {
        mockMvc.perform(get("/test/generic"))
                .andExpect(status().isInternalServerError());
    }

    @Test
    void businessException_responseMapping() {
        GlobalExceptionHandler handler = new GlobalExceptionHandler();
        ResponseEntity<?> response = handler.handleBusiness(new BusinessException(ResultCode.UNAUTHORIZED));
        assertEquals(HttpStatus.UNAUTHORIZED, response.getStatusCode());
    }
}
