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

@RestController
@RequestMapping("/api/admin/cache")
@RequiredArgsConstructor
public class AdminCacheController {

    private final ActivityLocalCache activityLocalCache;

    @GetMapping("/stats")
    public Result<CacheStatsVO> stats() {
        return Result.ok(CacheStatsVO.builder()
                .activityLocal(activityStats())
                .build());
    }

    @PostMapping("/activity-local/enabled")
    public Result<CacheStatsVO> setActivityLocalEnabled(@RequestParam boolean enabled) {
        activityLocalCache.setRuntimeEnabled(enabled);
        return stats();
    }

    @PostMapping("/activity-local/reset")
    public Result<CacheStatsVO> resetActivityLocal() {
        activityLocalCache.resetStatsAndEntries();
        return stats();
    }

    private ActivityLocalCacheStatsVO activityStats() {
        return ActivityLocalCacheStatsVO.builder()
                .enabled(activityLocalCache.enabled())
                .hitRate(activityLocalCache.hitRate())
                .requestCount(activityLocalCache.requestCount())
                .size(activityLocalCache.size())
                .build();
    }
}
