package com.shanpu.module.admin.domain.vo;

import lombok.Builder;
import lombok.Getter;

@Getter
@Builder
public class ActivityLocalCacheStatsVO {

    private boolean enabled;
    private double hitRate;
    private long requestCount;
    private long size;
}
