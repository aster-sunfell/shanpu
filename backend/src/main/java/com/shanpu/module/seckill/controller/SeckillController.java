package com.shanpu.module.seckill.controller;

import com.shanpu.common.annotation.RateLimit;
import com.shanpu.common.result.Result;
import com.shanpu.common.util.UserContext;
import com.shanpu.module.seckill.domain.vo.SeckillActivityVO;
import com.shanpu.module.seckill.domain.vo.SeckillGrabVO;
import com.shanpu.module.seckill.service.SeckillService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/seckills")
@RequiredArgsConstructor
public class SeckillController {

    private final SeckillService seckillService;

    @GetMapping
    public Result<List<SeckillActivityVO>> list(@RequestParam(required = false) Integer tier,
                                                @RequestParam(required = false) String status) {
        return Result.ok(seckillService.list(tier, status));
    }

    @GetMapping("/{id}")
    public Result<SeckillActivityVO> detail(@PathVariable Long id) {
        return Result.ok(seckillService.detail(id));
    }

    @RateLimit(window = 1, max = 5, dim = RateLimit.Dimension.USER)
    @PostMapping("/{id}/grab")
    public Result<SeckillGrabVO> grab(@PathVariable Long id) {
        return Result.ok(seckillService.grab(UserContext.requireUserId(), id));
    }
}
