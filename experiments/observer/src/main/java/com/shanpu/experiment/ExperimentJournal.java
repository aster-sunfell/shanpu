package com.shanpu.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Map;
import java.util.ArrayList;
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

    public synchronized Map<String, Object> snapshot() {
        return Map.of("enabled", enabled, "journal", path.toString(), "journalOffset", path.toFile().length(), "events", events.get(),
                "writeErrors", errors.get(), "syncAttempts", syncAttempts.get(), "asyncAttempts", asyncAttempts.get());
    }

    /** Bounded cursor reads let a remote runner collect evidence without a shared volume. */
    public synchronized Map<String, Object> readPage(long offset, long activityId) throws IOException {
        if (offset < 0 || offset > path.toFile().length()) {
            throw new IllegalArgumentException("Invalid journal cursor");
        }
        var entries = new ArrayList<Object>();
        if (!Files.exists(path)) {
            return Map.of("nextOffset", 0L, "events", entries, "hasMore", false);
        }
        try (var file = new RandomAccessFile(path.toFile(), "r")) {
            file.seek(offset);
            long next = offset;
            while (entries.size() < 1000 && file.getFilePointer() - offset < 1024 * 1024) {
                long start = file.getFilePointer();
                String line = file.readLine();
                if (line == null) break;
                long end = file.getFilePointer();
                file.seek(end - 1);
                if (file.readByte() != '\n') {
                    file.seek(start);
                    break;
                }
                file.seek(end);
                next = end;
                var event = mapper.readTree(line);
                if (event.path("activityId").asLong() == activityId) entries.add(event);
            }
            return Map.of("nextOffset", next, "events", entries, "hasMore", next < file.length());
        }
    }

    @PreDestroy
    public synchronized void close() throws IOException {
        if (writer != null) {
            writer.close();
        }
    }
}
