package com.shanpu.module.job.controller;

import com.shanpu.common.result.Result;
import com.shanpu.module.job.KafkaDlqRescueJob;
import com.shanpu.module.job.OrderReconcileJob;
import com.shanpu.module.job.OrderTimeoutJob;
import com.shanpu.module.job.SeckillWarmupJob;
import com.shanpu.module.job.StockReconcileJob;
import com.shanpu.module.job.domain.vo.DlqStatsVO;
import com.shanpu.module.job.domain.vo.JobRunVO;
import com.shanpu.module.seckill.service.OrderDlqLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/admin/jobs")
@RequiredArgsConstructor
public class AdminJobController {

    private final OrderTimeoutJob orderTimeoutJob;
    private final KafkaDlqRescueJob kafkaDlqRescueJob;
    private final StockReconcileJob stockReconcileJob;
    private final SeckillWarmupJob seckillWarmupJob;
    private final OrderReconcileJob orderReconcileJob;
    private final OrderDlqLogService orderDlqLogService;

    @PostMapping("/order-timeout/run")
    public Result<JobRunVO> runOrderTimeout() {
        return Result.ok(orderTimeoutJob.runOnce());
    }

    @PostMapping("/dlq-rescue/run")
    public Result<JobRunVO> runDlqRescue() {
        return Result.ok(kafkaDlqRescueJob.runOnce());
    }

    @PostMapping("/stock-reconcile/run")
    public Result<JobRunVO> runStockReconcile() {
        return Result.ok(stockReconcileJob.runOnce());
    }

    @PostMapping("/seckill-warmup/run")
    public Result<JobRunVO> runSeckillWarmup() {
        return Result.ok(seckillWarmupJob.runOnce());
    }

    @PostMapping("/order-reconcile/run")
    public Result<JobRunVO> runOrderReconcile() {
        return Result.ok(orderReconcileJob.runOnce());
    }

    @GetMapping("/dlq/stats")
    public Result<DlqStatsVO> dlqStats() {
        return Result.ok(orderDlqLogService.stats());
    }
}
