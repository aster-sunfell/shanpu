package com.shanpu.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.shanpu.cache.ActivityLocalCache;
import com.shanpu.cache.CacheClient;
import com.shanpu.common.exception.BusinessException;
import com.shanpu.module.order.domain.entity.Order;
import com.shanpu.module.order.mapper.OrderMapper;
import com.shanpu.module.seckill.domain.event.SeckillOrderPaidEvent;
import com.shanpu.module.seckill.domain.msg.SeckillOrderMsg;
import com.shanpu.module.seckill.mapper.SeckillActivityMapper;
import com.shanpu.module.seckill.service.impl.SeckillOrderWriter;
import com.shanpu.module.user.mapper.PointLogMapper;
import com.shanpu.module.user.mapper.UserMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.EnableAspectJAutoProxy;
import org.springframework.context.annotation.Import;
import org.springframework.core.env.MapPropertySource;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.TransactionSystemException;
import org.springframework.transaction.annotation.EnableTransactionManagement;
import org.springframework.transaction.support.AbstractPlatformTransactionManager;
import org.springframework.transaction.support.DefaultTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import javax.sql.DataSource;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/** Exercises the real Spring event, transaction, profile and AOP wiring without infrastructure. */
class ExperimentOrderObserverTest {
    @TempDir Path directory;

    static class Transactions extends AbstractPlatformTransactionManager {
        private final ThreadLocal<Boolean> active = new ThreadLocal<>();
        boolean failCommit;
        long committedAt;
        @Override protected Object doGetTransaction() { return Boolean.TRUE.equals(active.get()); }
        @Override protected boolean isExistingTransaction(Object transaction) { return Boolean.TRUE.equals(transaction); }
        @Override protected void doBegin(Object transaction, TransactionDefinition definition) { active.set(true); }
        @Override protected void doCommit(DefaultTransactionStatus status) {
            if (failCommit) throw new TransactionSystemException("commit failed");
            committedAt = System.currentTimeMillis();
        }
        @Override protected void doRollback(DefaultTransactionStatus status) { }
        @Override protected void doCleanupAfterCompletion(Object transaction) { active.remove(); }
    }

    @Configuration(proxyBeanMethods = false)
    @EnableAspectJAutoProxy
    @EnableTransactionManagement
    @Import({ExperimentJournal.class, ExperimentOrderObserver.class, ExperimentMetrics.class, ExperimentMetrics.Controller.class})
    static class Config {
        @Bean Transactions transactionManager() { return new Transactions(); }
        @Bean TransactionTemplate transactionTemplate(Transactions manager) { return new TransactionTemplate(manager); }
        @Bean ObjectMapper objectMapper() { return new ObjectMapper(); }
        @Bean SeckillActivityMapper activities() { return mock(SeckillActivityMapper.class); }
        @Bean OrderMapper orders() { return mock(OrderMapper.class); }
        @Bean UserMapper users() { return mock(UserMapper.class); }
        @Bean PointLogMapper logs() { return mock(PointLogMapper.class); }
        @Bean CacheClient cacheClient() { return mock(CacheClient.class); }
        @Bean DataSource dataSource() { return mock(DataSource.class); }
        @Bean ActivityLocalCache localCache() { return mock(ActivityLocalCache.class); }
        @Bean SeckillOrderWriter writer(SeckillActivityMapper activities, OrderMapper orders, UserMapper users,
                                       PointLogMapper logs, TransactionTemplate transactions, CacheClient cache,
                                       ApplicationEventPublisher publisher) {
            return new SeckillOrderWriter(activities, orders, users, logs, transactions, cache, publisher);
        }
    }

    private AnnotationConfigApplicationContext context(String profile, Path journal) {
        var context = new AnnotationConfigApplicationContext();
        context.getEnvironment().setActiveProfiles(profile);
        context.getEnvironment().getPropertySources().addFirst(new MapPropertySource("test", Map.of(
                "shanpu.experiment.enabled", "true", "shanpu.experiment.journal", journal.toString())));
        context.register(Config.class);
        context.refresh();
        when(context.getBean(SeckillActivityMapper.class).deductStock(12L)).thenReturn(1);
        when(context.getBean(UserMapper.class).deductPoints(34L, 100)).thenReturn(1);
        when(context.getBean(UserMapper.class).selectPointsById(34L)).thenReturn(900);
        when(context.getBean(OrderMapper.class).insertIgnore(any(Order.class))).thenReturn(1);
        return context;
    }

    private void sync(AnnotationConfigApplicationContext context) {
        context.getBean(SeckillOrderWriter.class).createSyncPaidOrder(34L, "56", 12L, 1L, 100, Order.SOURCE_SECKILL_FLASH);
    }

    private SeckillOrderMsg message() {
        return SeckillOrderMsg.builder().activityId(12L).userId(34L).orderId("56")
                .productId(1L).price(100).source(Order.SOURCE_SECKILL_SUPER).build();
    }

    @Test
    void ordinaryProfileCreatesNoExperimentBeansEvenWhenEnabledPropertyIsTrue() {
        Path file = directory.resolve("normal.jsonl");
        try (var context = context("dev", file)) {
            sync(context);
            assertTrue(context.getBeansOfType(ExperimentJournal.class).isEmpty());
            assertTrue(context.getBeansOfType(ExperimentOrderObserver.class).isEmpty());
            assertTrue(context.getBeansOfType(ExperimentMetrics.class).isEmpty());
            assertTrue(context.getBeansOfType(ExperimentMetrics.Controller.class).isEmpty());
            assertFalse(Files.exists(file));
            verify(context.getBean(PointLogMapper.class)).insert(any());
        }
    }

    @Test
    void syncCommitRetainsAttemptsAndJournalFormat() throws Exception {
        Path file = directory.resolve("sync.jsonl");
        try (var context = context("experiment", file)) {
            sync(context);
            var event = new ObjectMapper().readTree(Files.readString(file));
            assertEquals("56", event.get("orderId").asText());
            assertTrue(event.get("commitObservedAtMs").asLong() >= context.getBean(Transactions.class).committedAt);
            assertEquals(1L, context.getBean(ExperimentJournal.class).snapshot().get("syncAttempts"));
            assertEquals(1L, context.getBean(ExperimentJournal.class).snapshot().get("events"));
            assertNotNull(context.getBean(ExperimentMetrics.Controller.class));
        }
    }

    @Test
    void asyncDuplicateCountsAttemptWithoutPublishingAnotherCompletion() throws Exception {
        Path file = directory.resolve("async.jsonl");
        try (var context = context("experiment", file)) {
            var writer = context.getBean(SeckillOrderWriter.class);
            assertTrue(writer.createAsyncPaidOrder(message()));
            when(context.getBean(OrderMapper.class).existsByOrderId("56")).thenReturn(1);
            assertFalse(writer.createAsyncPaidOrder(message()));
            assertEquals(1, Files.readAllLines(file).size());
            assertEquals(2L, context.getBean(ExperimentJournal.class).snapshot().get("asyncAttempts"));
            assertEquals(1L, context.getBean(ExperimentJournal.class).snapshot().get("events"));
        }
    }

    @Test
    void insertIgnoreDuplicateDoesNotPublishCompletion() {
        Path file = directory.resolve("ignored.jsonl");
        try (var context = context("experiment", file)) {
            when(context.getBean(OrderMapper.class).existsByOrderId("56")).thenReturn(0, 1);
            when(context.getBean(OrderMapper.class).insertIgnore(any(Order.class))).thenReturn(0);
            assertFalse(context.getBean(SeckillOrderWriter.class).createAsyncPaidOrder(message()));
            assertFalse(Files.exists(file));
            assertEquals(1L, context.getBean(ExperimentJournal.class).snapshot().get("asyncAttempts"));
        }
    }

    @Test
    void failedOrderStillCountsAttemptButNeverCompletion() {
        Path file = directory.resolve("rollback.jsonl");
        try (var context = context("experiment", file)) {
            when(context.getBean(UserMapper.class).deductPoints(34L, 100)).thenReturn(0);
            assertThrows(BusinessException.class, () -> sync(context));
            assertFalse(Files.exists(file));
            assertEquals(1L, context.getBean(ExperimentJournal.class).snapshot().get("syncAttempts"));
            assertEquals(0L, context.getBean(ExperimentJournal.class).snapshot().get("events"));
        }
    }

    @Test
    void joinedOuterTransactionDoesNotRecordAnOrderThatLaterRollsBack() {
        Path file = directory.resolve("outer-rollback.jsonl");
        try (var context = context("experiment", file)) {
            assertThrows(IllegalStateException.class, () -> context.getBean(TransactionTemplate.class).executeWithoutResult(status -> {
                sync(context);
                assertFalse(Files.exists(file));
                throw new IllegalStateException("outer rollback");
            }));
            assertFalse(Files.exists(file));
            assertEquals(0L, context.getBean(ExperimentJournal.class).snapshot().get("events"));
        }
    }

    @Test
    void failedCommitAndEventOutsideTransactionNeverCountAsCompletion() {
        Path file = directory.resolve("commit-failure.jsonl");
        try (var context = context("experiment", file)) {
            context.publishEvent(new SeckillOrderPaidEvent(12L, 34L, "outside"));
            assertFalse(Files.exists(file));
            context.getBean(Transactions.class).failCommit = true;
            assertThrows(TransactionSystemException.class, () -> sync(context));
            assertFalse(Files.exists(file));
            assertEquals(0L, context.getBean(ExperimentJournal.class).snapshot().get("events"));
        }
    }

    @Test
    void journalWriteFailureCannotFailAnAlreadyCommittedOrder() throws Exception {
        Path parent = directory.resolve("occupied");
        Files.writeString(parent, "file instead of directory");
        try (var context = context("experiment", parent.resolve("commits.jsonl"))) {
            assertDoesNotThrow(() -> sync(context));
            assertTrue(context.getBean(Transactions.class).committedAt > 0);
            assertEquals(1L, context.getBean(ExperimentJournal.class).snapshot().get("writeErrors"));
            assertEquals(0L, context.getBean(ExperimentJournal.class).snapshot().get("events"));
        }
    }
}
