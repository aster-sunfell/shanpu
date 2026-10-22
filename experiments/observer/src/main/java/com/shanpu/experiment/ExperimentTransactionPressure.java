package com.shanpu.experiment;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.LongSupplier;

/** Experiment-only: first mapper invocation in an acquired transaction through commit/rollback.
 * Connection acquisition waits are excluded. Retries and rollbacks still consume DB capacity.
 * Experiments serialize activities; only the current activity's bounded time series is retained.
 */
@Component
@Profile("experiment")
public class ExperimentTransactionPressure {
    private final boolean enabled;
    private final LongSupplier clock;
    private final ThreadLocal<Long> activity = new ThreadLocal<>();
    private final TreeMap<Long, Bucket> buckets = new TreeMap<>();
    private long activityId, started, ended, errors, windowId;
    private int active, peak;

    @Autowired
    public ExperimentTransactionPressure(@Value("${shanpu.experiment.enabled:false}") boolean enabled) {
        this(enabled, System::currentTimeMillis);
    }

    ExperimentTransactionPressure(boolean enabled, LongSupplier clock) {
        this.enabled = enabled;
        this.clock = clock;
    }

    Long enter(long id) { Long previous = activity.get(); activity.set(id); return previous; }
    void leave(Long previous) { if (previous == null) activity.remove(); else activity.set(previous); }

    public void beforeMapper() {
        Long id = activity.get();
        if (!enabled || id == null || !TransactionSynchronizationManager.isActualTransactionActive()
                || !TransactionSynchronizationManager.isSynchronizationActive()) return;
        // Multiple mapper calls, or joined writer calls, belong to one physical transaction.
        if (TransactionSynchronizationManager.getSynchronizations().stream().anyMatch(s -> s instanceof Completion)) return;
        if (!begin(id)) return;
        TransactionSynchronizationManager.registerSynchronization(new Completion());
    }

    /** Called only after fixture work and the previous measurement have drained. */
    public synchronized Map<String, Object> openWindow(long id) {
        if (!enabled || id <= 0 || id != activityId || active != 0 || started != ended || errors != 0)
            throw new IllegalStateException("Transaction window requires the prepared activity to be idle");
        started = ended = errors = 0;
        active = peak = 0;
        buckets.clear();
        windowId++;
        return snapshot();
    }

    private synchronized boolean begin(long id) {
        if (id != activityId) {
            if (active != 0) { errors++; return false; }
            activityId = id;
            windowId = 0;
            started = ended = errors = 0;
            active = peak = 0;
            buckets.clear();
        }
        Bucket bucket = bucket();
        if (bucket == null) return false;
        started++;
        active++;
        peak = Math.max(peak, active);
        bucket.starts++;
        bucket.peak = Math.max(bucket.peak, active);
        bucket.activeEnd = active;
        return true;
    }

    private synchronized void finish(int status) {
        Bucket bucket = bucket();
        active--;
        ended++;
        if (bucket != null) {
            bucket.ends++;
            if (status != TransactionSynchronization.STATUS_COMMITTED) bucket.notCommitted++;
            bucket.activeEnd = active;
        }
    }

    private Bucket bucket() {
        long second = Math.floorDiv(clock.getAsLong(), 1000);
        if (!buckets.isEmpty() && second < buckets.lastKey()) { errors++; return null; }
        if (!buckets.containsKey(second) && buckets.size() >= 4096) { errors++; return null; }
        return buckets.computeIfAbsent(second, ignored -> new Bucket(active));
    }

    public synchronized Map<String, Object> snapshot() {
        var rows = new ArrayList<Map<String, Object>>();
        buckets.forEach((second, b) -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("epochSecond", second);
            row.put("starts", b.starts);
            row.put("ends", b.ends);
            row.put("notCommitted", b.notCommitted);
            row.put("activePeak", b.peak);
            row.put("activeEnd", b.activeEnd);
            rows.add(row);
        });
        return Map.of("version", 2, "windowId", windowId, "activityId", activityId, "started", started, "ended", ended,
                "active", active, "peakActive", peak, "errors", errors, "buckets", rows);
    }

    private class Completion implements TransactionSynchronization {
        private boolean finished;
        // Close before other after-commit evidence callbacks, whose file IO is not DB work.
        @Override public int getOrder() { return org.springframework.core.Ordered.HIGHEST_PRECEDENCE; }
        @Override public void afterCommit() { close(TransactionSynchronization.STATUS_COMMITTED); }
        @Override public void afterCompletion(int status) { close(status); }
        private void close(int status) {
            if (!finished) { finished = true; finish(status); }
        }
    }

    private static class Bucket {
        long starts, ends, notCommitted;
        int peak, activeEnd;
        Bucket(int active) { peak = activeEnd = active; }
    }
}
