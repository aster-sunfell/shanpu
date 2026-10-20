package com.shanpu.experiment;

import com.alibaba.druid.pool.DruidDataSource;
import com.shanpu.cache.ActivityLocalCache;
import com.shanpu.common.result.Result;
import lombok.RequiredArgsConstructor;
import org.apache.ibatis.executor.Executor;
import org.apache.ibatis.mapping.MappedStatement;
import org.apache.ibatis.plugin.Interceptor;
import org.apache.ibatis.plugin.Intercepts;
import org.apache.ibatis.plugin.Invocation;
import org.apache.ibatis.plugin.Signature;
import org.apache.ibatis.session.ResultHandler;
import org.apache.ibatis.session.RowBounds;
import org.springframework.context.annotation.Profile;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.sql.DataSource;
import java.lang.management.ManagementFactory;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.LongAdder;

/** Counts mapper calls (including failed calls), not estimated database statement counts. */
@Component
@Profile("experiment")
@Intercepts({
        @Signature(type = Executor.class, method = "update", args = {MappedStatement.class, Object.class}),
        @Signature(type = Executor.class, method = "query", args = {
                MappedStatement.class, Object.class, RowBounds.class, ResultHandler.class})
})
public class ExperimentMetrics implements Interceptor {
    private final Map<String, LongAdder> calls = new ConcurrentHashMap<>();

    @Override
    public Object intercept(Invocation invocation) throws Throwable {
        calls.computeIfAbsent(((MappedStatement) invocation.getArgs()[0]).getId(), k -> new LongAdder()).increment();
        return invocation.proceed();
    }

    public Map<String, Long> snapshot() {
        Map<String, Long> result = new LinkedHashMap<>();
        calls.forEach((key, value) -> result.put(key, value.sum()));
        return result;
    }

    @RestController
    @Profile("experiment")
    @RequiredArgsConstructor
    public static class Controller {
        private final ExperimentMetrics metrics;
        private final ExperimentJournal journal;
        private final DataSource dataSource;
        private final ActivityLocalCache cache;
        private final Environment environment;

        @GetMapping("/api/admin/experiments/metrics")
        public Result<Map<String, Object>> metrics() {
            Map<String, Object> result = new LinkedHashMap<>(journal.snapshot());
            result.put("sqlCalls", metrics.snapshot());
            result.put("localCacheEnabled", cache.enabled());
            result.put("timeMs", System.currentTimeMillis());
            result.put("processStartedAtMs", ManagementFactory.getRuntimeMXBean().getStartTime());
            Map<String, Object> settings = new LinkedHashMap<>();
            for (String key : new String[]{"server.tomcat.threads.max", "spring.datasource.druid.max-active",
                    "spring.datasource.druid.max-wait", "spring.kafka.producer.acks",
                    "spring.kafka.producer.properties.linger.ms", "logging.level.com.shanpu",
                    "shanpu.kafka.dlq-rescue.cron", "shanpu.order.timeout.cron", "shanpu.order.reconcile.cron",
                    "shanpu.seckill.warmup.cron", "shanpu.seckill.stock-reconcile.cron"}) {
                settings.put(key, environment.getProperty(key, "unset"));
            }
            settings.put("profiles", environment.getActiveProfiles());
            settings.put("database", environment.getProperty("spring.datasource.url", "")
                    .replaceFirst("^jdbc:mysql://[^/]+/([^?]+).*$", "$1"));
            settings.put("consumerConcurrency", 3);
            settings.put("javaVersion", System.getProperty("java.version"));
            settings.put("heapMax", ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getMax());
            result.put("settings", settings);
            result.put("heapUsed", ManagementFactory.getMemoryMXBean().getHeapMemoryUsage().getUsed());
            result.put("gcTimeMs", ManagementFactory.getGarbageCollectorMXBeans().stream()
                    .mapToLong(gc -> Math.max(0, gc.getCollectionTime())).sum());
            if (ManagementFactory.getOperatingSystemMXBean() instanceof com.sun.management.OperatingSystemMXBean os) {
                result.put("processCpuTimeNs", os.getProcessCpuTime());
            }
            if (dataSource instanceof DruidDataSource pool) {
                result.put("poolActive", pool.getActiveCount());
                result.put("poolWaitThreads", pool.getWaitThreadCount());
                result.put("poolWaitCount", pool.getNotEmptyWaitCount());
                result.put("poolWaitMs", pool.getNotEmptyWaitMillis());
            }
            return Result.ok(result);
        }
    }
}
