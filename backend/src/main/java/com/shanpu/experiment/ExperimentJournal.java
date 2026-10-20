package com.shanpu.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/** Optional local experiment evidence. Failures must never roll back a committed order. */
@Component
@Profile("experiment")
public class ExperimentJournal {
    private final boolean enabled;
    private final Path path;
    private final ObjectMapper mapper;
    private BufferedWriter writer;
    private final AtomicLong events = new AtomicLong();
    private final AtomicLong errors = new AtomicLong();
    private final AtomicLong syncAttempts = new AtomicLong();
    private final AtomicLong asyncAttempts = new AtomicLong();

    public ExperimentJournal(@Value("${shanpu.experiment.enabled:false}") boolean enabled,
                             @Value("${shanpu.experiment.journal:.local/experiments/commits.jsonl}") String path,
                             ObjectMapper mapper) {
        this.enabled = enabled;
        this.path = Path.of(path).toAbsolutePath().normalize();
        this.mapper = mapper;
    }

    public void attempt(boolean async) {
        if (enabled) {
            (async ? asyncAttempts : syncAttempts).incrementAndGet();
        }
    }

    public void recordCommittedOrder(Long activityId, Long userId, String orderId) {
        long observedAt = System.currentTimeMillis();
        if (!enabled) {
            return;
        }
        synchronized (this) {
            try {
                if (writer == null) {
                    Files.createDirectories(path.getParent());
                    writer = Files.newBufferedWriter(path, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
                }
                writer.write(mapper.writeValueAsString(Map.of(
                        "activityId", activityId, "userId", userId, "orderId", orderId,
                        "commitObservedAtMs", observedAt)));
                writer.newLine();
                writer.flush();
                events.incrementAndGet();
            } catch (IOException | RuntimeException e) {
                errors.incrementAndGet();
            }
        }
    }

    public Map<String, Object> snapshot() {
        return Map.of("enabled", enabled, "journal", path.toString(), "events", events.get(),
                "writeErrors", errors.get(), "syncAttempts", syncAttempts.get(), "asyncAttempts", asyncAttempts.get());
    }

    @PreDestroy
    public synchronized void close() throws IOException {
        if (writer != null) {
            writer.close();
        }
    }
}
