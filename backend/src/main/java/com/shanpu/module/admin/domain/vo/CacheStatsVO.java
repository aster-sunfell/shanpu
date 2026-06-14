package com.shanpu.module.admin.domain.vo;

import lombok.Builder;
import lombok.Getter;

@Getter
@Builder
public class CacheStatsVO {

    private ActivityLocalCacheStatsVO activityLocal;
}
