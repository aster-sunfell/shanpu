package com.shanpu.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.annotation.Profile;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Bounded observation inside the same SUT quota, independent of business HTTP admission. */
@Component
@Profile("experiment")
@Slf4j
public class ExperimentEvidencePublisher {
    private final ExperimentMetrics.Controller metrics;
    private final ObjectMapper mapper;
    private final Path destination;
    private boolean publicationFailed;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(task -> {
        Thread thread = new Thread(task, "experiment-evidence");
        thread.setDaemon(true);
        return thread;
    });

    public ExperimentEvidencePublisher(ExperimentMetrics.Controller metrics, ObjectMapper mapper,
            @Value("${shanpu.experiment.journal:/evidence/commits.jsonl}") String journal) {
        this.metrics = metrics;
        this.mapper = mapper;
        this.destination = Path.of(journal).toAbsolutePath().normalize().resolveSibling("metrics.json");
    }

    @EventListener(ApplicationReadyEvent.class)
    public void start() throws IOException {
        publish();
        scheduler.scheduleWithFixedDelay(() -> {
            try {
                publish();
                publicationFailed = false;
            } catch (IOException | RuntimeException error) {
                // Preserve the last complete sample. The reader rejects stale evidence.
                if (!publicationFailed) log.warn("Experiment snapshot publication failed; retaining last complete snapshot", error);
                publicationFailed = true;
            }
        }, 1, 1, TimeUnit.SECONDS);
    }

    void publish() throws IOException {
        Files.createDirectories(destination.getParent());
        Path temporary = destination.resolveSibling("metrics.json.tmp");
        mapper.writeValue(temporary.toFile(), metrics.metrics().getData());
        Files.move(temporary, destination, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }

    @PreDestroy
    public void close() {
        scheduler.shutdownNow();
    }
}
