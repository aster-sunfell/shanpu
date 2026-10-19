package com.shanpu.module.admin.controller;

import com.shanpu.cache.ActivityLocalCache;
import com.shanpu.common.result.Result;
import com.shanpu.module.admin.domain.vo.ActivityLocalCacheStatsVO;
import com.shanpu.module.admin.domain.vo.CacheStatsVO;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 缓存管理接口。
 *
 * <p>当前管理面只暴露秒杀活动详情 L1 本地缓存的开关、重置和统计能力。
 * k6 场景 D 通过这些接口在同一后端进程内切换 Caffeine + Redis 与纯 Redis 路径，
 * 并读取命中率作为压测验收指标。</p>
 */
@RestController
@RequestMapping("/api/admin/cache")
@RequiredArgsConstructor
public class AdminCacheController {

    private final ActivityLocalCache activityLocalCache;

    /**
     * 查询当前缓存统计。
     *
     * @return 活动详情 L1 的开关、命中率、请求数和条目数
     */
    @GetMapping("/stats")
    public Result<CacheStatsVO> stats() {
        return Result.ok(CacheStatsVO.builder()
                .activityLocal(activityStats())
                .build());
    }

    /**
     * 运行时开启或关闭活动详情 L1。
     *
     * <p>关闭时会清空 Caffeine 条目，使后续请求真实回到 Redis 路径。</p>
     *
     * @param enabled true 表示开启活动 L1，false 表示关闭活动 L1
     * @return 切换后的缓存统计
     */
    @PostMapping("/activity-local/enabled")
    public Result<CacheStatsVO> setActivityLocalEnabled(@RequestParam boolean enabled) {
        activityLocalCache.setRuntimeEnabled(enabled);
        return stats();
    }

    /**
     * 清空活动详情 L1 条目和统计计数。
     *
     * <p>每轮压测前调用该接口，避免上一轮命中率和缓存条目影响当前轮次。</p>
     *
     * @return 重置后的缓存统计
     */
    @PostMapping("/activity-local/reset")
    public Result<CacheStatsVO> resetActivityLocal() {
        activityLocalCache.resetStatsAndEntries();
        return stats();
    }

    /**
     * 组装活动详情 L1 统计视图。
     *
     * @return 活动 L1 统计
     */
    private ActivityLocalCacheStatsVO activityStats() {
        return ActivityLocalCacheStatsVO.builder()
                .enabled(activityLocalCache.enabled())
                .hitRate(activityLocalCache.hitRate())
                .requestCount(activityLocalCache.requestCount())
                .size(activityLocalCache.size())
                .build();
    }
}
