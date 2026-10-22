package com.shanpu.module.seckill.infra;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.jupiter.api.Assertions.*;

@Timeout(10)
class OrderConsumptionPacerTest {
    @Test void idleAndSchedulerPausesDoNotAccumulateCredits() {
        var now = new AtomicLong(-1_000_000_000L); // nanoTime need not be positive.
        var waits = new ArrayList<Long>();
        var pacer = new OrderConsumptionPacer(400, now::get, (condition, nanos) -> {
            waits.add(nanos);
            now.addAndGet(nanos);
        });
        pacer.acquire();
        now.addAndGet(TimeUnit.SECONDS.toNanos(10));
        pacer.acquire();
        pacer.acquire();
        assertEquals(List.of(2_500_000L), waits);
        now.addAndGet(TimeUnit.MILLISECONDS.toNanos(100));
        pacer.acquire();
        pacer.acquire();
        assertEquals(List.of(2_500_000L, 2_500_000L), waits);
    }

    @Test void earlyWakeupsRecheckDeadlineAndClockWrapIsSafe() {
        var now = new AtomicLong(Long.MAX_VALUE - 1_000_000);
        var waits = new AtomicInteger();
        var pacer = new OrderConsumptionPacer(400, now::get, (condition, nanos) -> {
            if (waits.incrementAndGet() > 1) now.addAndGet(nanos);
        });
        pacer.acquire();
        pacer.acquire();
        assertEquals(2, waits.get());
    }

    @Test void threeThreadsShareOneBudget() throws Exception {
        var now = new AtomicLong();
        var pacer = new OrderConsumptionPacer(100, now::get, (condition, nanos) -> now.addAndGet(nanos));
        var pool = Executors.newFixedThreadPool(3);
        var ready = new CountDownLatch(3);
        var start = new CountDownLatch(1);
        try {
            var tasks = new ArrayList<java.util.concurrent.Future<?>>();
            for (int t=0; t<3; t++) tasks.add(pool.submit(() -> {
                ready.countDown();
                try { start.await(); } catch (InterruptedException e) { throw new RuntimeException(e); }
                for (int i=0; i<10; i++) pacer.acquire();
            }));
            assertTrue(ready.await(2, TimeUnit.SECONDS));
            start.countDown();
            for (var task : tasks) task.get(5, TimeUnit.SECONDS);
            // Exactly 29 shared intervals, regardless of which consumer wins the lock.
            assertEquals(290_000_000L, now.get());
        } finally { pool.shutdownNow(); }
    }

    @Test void waitingIsInterruptibleAndRestoresInterruptFlag() throws Exception {
        var waiting = new CountDownLatch(1);
        var pacer = new OrderConsumptionPacer(1, System::nanoTime, (condition, nanos) -> {
            waiting.countDown();
            condition.awaitNanos(nanos);
        });
        pacer.acquire();
        var error = new AtomicReference<Throwable>();
        var interrupted = new AtomicReference<Boolean>();
        var thread = new Thread(() -> {
            try { pacer.acquire(); } catch (Throwable e) { error.set(e); interrupted.set(Thread.currentThread().isInterrupted()); }
        });
        thread.start();
        try {
            assertTrue(waiting.await(2, TimeUnit.SECONDS));
            thread.interrupt();
            thread.join(2000);
            assertFalse(thread.isAlive());
            assertInstanceOf(OrderConsumptionPacer.ConsumptionInterruptedException.class, error.get());
            assertEquals(true, interrupted.get());
        } finally { thread.interrupt(); thread.join(2000); }
    }

    @Test void preInterruptedThreadCannotStartWork() {
        Thread.currentThread().interrupt();
        try {
            assertThrows(OrderConsumptionPacer.ConsumptionInterruptedException.class,
                    () -> new OrderConsumptionPacer(400).acquire());
            assertTrue(Thread.currentThread().isInterrupted());
        } finally { Thread.interrupted(); }
    }

    @Test void invalidRatesFailInsteadOfDisablingProtection() {
        for (int rate : new int[]{0, -1, Integer.MAX_VALUE})
            assertThrows(IllegalArgumentException.class, () -> new OrderConsumptionPacer(rate));
    }
}
