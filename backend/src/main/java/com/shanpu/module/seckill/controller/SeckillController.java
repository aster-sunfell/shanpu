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

    /**
     * 查询秒杀活动列表。
     *
     * @param tier 可选档位过滤
     * @param status 可选状态过滤
     * @return 活动列表响应
     */
    @GetMapping
    public Result<List<SeckillActivityVO>> list(@RequestParam(required = false) Integer tier,
                                                @RequestParam(required = false) String status) {
        return Result.ok(seckillService.list(tier, status));
    }

    /**
     * 查询单个秒杀活动详情。
     *
     * @param id 活动 ID
     * @return 活动详情响应
     */
    @GetMapping("/{id}")
    public Result<SeckillActivityVO> detail(@PathVariable Long id) {
        return Result.ok(seckillService.detail(id));
    }

    /**
     * 提交秒杀抢购请求。
     *
     * <p>用户维度限流先拦截高频点击，进入服务层后再按活动档位选择
     * MySQL 行锁、Redis + Lua 同步落库或 Redis + Lua + Kafka 异步落库链路。</p>
     *
     * @param id 活动 ID
     * @return 抢购结果响应
     */
    @RateLimit(window = 1, max = 5, dim = RateLimit.Dimension.USER)
    @PostMapping("/{id}/grab")
    public Result<SeckillGrabVO> grab(@PathVariable Long id) {
        // 用户维度限流通过后，服务层继续执行时间窗口校验、库存扣减和下单链路。
        return Result.ok(seckillService.grab(UserContext.requireUserId(), id));
    }
}
