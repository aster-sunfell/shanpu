package com.shanpu.cache;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Getter
@Setter
@Component
@ConfigurationProperties(prefix = "shanpu.cache.local.activity")
public class ActivityLocalCacheProperties {

    private boolean enabled = true;
    private long maxSize = 256;
    private long expireMs = 1000;
}
