package com.shanpu.module.seckill.infra;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.concurrent.locks.Condition;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.LongSupplier;

/** Single-instance pacing shared by all order consumers; idle time grants no burst credit. */
@Component
public class OrderConsumptionPacer {
    public static final String MODE = "smooth-no-catch-up";
    private final int transactionsPerSecond;
    private final long intervalNanos;
    private final LongSupplier clock;
    private final Waiter waiter;
    private final ReentrantLock lock = new ReentrantLock(true);
    private final Condition changed = lock.newCondition();
    private boolean granted;
    private long nextAllowed;

    @Autowired
    public OrderConsumptionPacer(
            @Value("${shanpu.kafka.consumer.max-transactions-per-second}") int transactionsPerSecond) {
        this(transactionsPerSecond, System::nanoTime, (condition, nanos) -> condition.awaitNanos(nanos));
    }

    // Injectable monotonic clock/wait for deterministic pause, idle and wakeup tests.
    OrderConsumptionPacer(int transactionsPerSecond, LongSupplier clock, Waiter waiter) {
        if (transactionsPerSecond <= 0 || transactionsPerSecond > 1_000_000_000) {
            throw new IllegalArgumentException("Kafka transaction rate must be between 1 and 1000000000 per second");
        }
        this.transactionsPerSecond = transactionsPerSecond;
        this.intervalNanos = (1_000_000_000L + transactionsPerSecond - 1) / transactionsPerSecond;
        this.clock = clock;
        this.waiter = waiter;
    }

    public int transactionsPerSecond() { return transactionsPerSecond; }

    /** Called before opening a transaction, including retries and idempotency checks. */
    public void acquire() {
        try {
            lock.lockInterruptibly();
            try {
                while (true) {
                    if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
                    long now = clock.getAsLong();
                    long remaining = nextAllowed - now;
                    if (!granted || remaining <= 0) {
                        granted = true;
                        // Base on actual grant time: never catch up after GC, idle or scheduling pauses.
                        nextAllowed = now + intervalNanos;
                        return;
                    }
                    // Releases the lock while waiting; recheck after every wakeup.
                    waiter.await(changed, remaining);
                }
            } finally {
                lock.unlock();
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new ConsumptionInterruptedException(error);
        }
    }

    @FunctionalInterface
    interface Waiter { void await(Condition condition, long nanos) throws InterruptedException; }

    /** Routed to container stopping, never business retry exhaustion or DLQ recovery. */
    public static class ConsumptionInterruptedException extends RuntimeException {
        public ConsumptionInterruptedException(InterruptedException cause) {
            super("Order consumption interrupted before starting a database transaction", cause);
        }
    }
}
