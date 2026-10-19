package com.shanpu.module.admin.domain.vo;

import lombok.Builder;
import lombok.Getter;

/**
 * 缓存统计聚合视图。
 *
 * <p>当前只暴露秒杀活动详情的 Caffeine L1 指标，后续如增加其他本地缓存可在此扩展。</p>
 */
@Getter
@Builder
public class CacheStatsVO {

    /**
     * 秒杀活动详情 L1 本地缓存统计。
     */
    private ActivityLocalCacheStatsVO activityLocal;
}
