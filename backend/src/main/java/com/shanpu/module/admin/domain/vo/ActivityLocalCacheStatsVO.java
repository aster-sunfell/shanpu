package com.shanpu.module.admin.domain.vo;

import lombok.Builder;
import lombok.Getter;

/**
 * 活动详情 L1 本地缓存统计视图。
 *
 * <p>该对象由管理接口返回，供压测脚本读取 Caffeine 是否开启、命中率和当前条目数。</p>
 */
@Getter
@Builder
public class ActivityLocalCacheStatsVO {

    /**
     * 当前活动 L1 是否实际启用。
     */
    private boolean enabled;

    /**
     * L1 命中率，计算口径为 hitCount / requestCount。
     */
    private double hitRate;

    /**
     * 进入活动 L1 查询路径的请求数。
     */
    private long requestCount;

    /**
     * 当前 Caffeine 中的活动详情条目数。
     */
    private long size;
}
