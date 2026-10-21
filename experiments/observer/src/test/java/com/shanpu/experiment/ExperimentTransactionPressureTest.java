package com.shanpu.experiment;

import org.apache.ibatis.executor.Executor;
import org.apache.ibatis.mapping.MappedStatement;
import org.apache.ibatis.mapping.SqlCommandType;
import org.apache.ibatis.builder.StaticSqlSource;
import org.apache.ibatis.plugin.Invocation;
import org.apache.ibatis.session.Configuration;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class ExperimentTransactionPressureTest {
    private void begin() {
        TransactionSynchronizationManager.setActualTransactionActive(true);
        TransactionSynchronizationManager.initSynchronization();
    }
    private void end(int status) {
        try {
            if (status == TransactionSynchronization.STATUS_COMMITTED)
                TransactionSynchronizationManager.getSynchronizations().forEach(TransactionSynchronization::afterCommit);
            TransactionSynchronizationManager.getSynchronizations().forEach(s -> s.afterCompletion(status));
        } finally { TransactionSynchronizationManager.clear(); }
    }
    @SuppressWarnings("unchecked")
    private List<Map<String,Object>> rows(ExperimentTransactionPressure pressure) {
        return (List<Map<String,Object>>) pressure.snapshot().get("buckets");
    }

    @Test void actualMyBatisInterceptorCountsOneAcquiredTransactionNotEveryMapperOrConnectionWait() throws Throwable {
        AtomicLong now = new AtomicLong(1000);
        var pressure = new ExperimentTransactionPressure(true, now::get);
        var metrics = new ExperimentMetrics(pressure);
        var config = new Configuration();
        var statement = new MappedStatement.Builder(config, "test.update", new StaticSqlSource(config, "UPDATE test"), SqlCommandType.UPDATE).build();
        var invocation = new Invocation(mock(Executor.class), Executor.class.getMethod("update", MappedStatement.class, Object.class), new Object[]{statement, null});
        pressure.enter(12);
        // Entering the writer (including pool wait) is not an acquired transaction.
        metrics.intercept(invocation);
        assertEquals(0L, pressure.snapshot().get("started"));
        begin();
        try {
            metrics.intercept(invocation);
            metrics.intercept(invocation);
            assertEquals(1L, pressure.snapshot().get("started"));
            assertEquals(1, pressure.snapshot().get("active"));
            now.set(4000);
        } finally { end(TransactionSynchronization.STATUS_ROLLED_BACK); pressure.leave(null); }
        assertEquals(0, pressure.snapshot().get("active"));
        assertEquals(1L, pressure.snapshot().get("ended"));
        assertEquals(1L, rows(pressure).get(1).get("notCommitted"));
        assertEquals(1, rows(pressure).get(1).get("activePeak"));
    }

    interface OrderSql {
        @org.apache.ibatis.annotations.Update("UPDATE orders SET stock=stock-1 WHERE id=1")
        int update();
    }

    @Test void springMyBatisJdbcWiringExcludesConnectionAcquisitionAndEndsAtCommit() throws Exception {
        var pressure = new ExperimentTransactionPressure(true);
        var source = mock(javax.sql.DataSource.class);
        var connection = mock(java.sql.Connection.class);
        var statement = mock(java.sql.PreparedStatement.class);
        when(source.getConnection()).thenAnswer(call -> {
            assertEquals(0, pressure.snapshot().get("active"));
            return connection;
        });
        when(connection.getAutoCommit()).thenReturn(true);
        when(connection.prepareStatement(anyString())).thenReturn(statement);
        when(statement.getUpdateCount()).thenReturn(1);
        var factory = new org.mybatis.spring.SqlSessionFactoryBean();
        factory.setDataSource(source);
        factory.setPlugins(new ExperimentMetrics(pressure));
        var configuration = new Configuration();
        configuration.addMapper(OrderSql.class);
        factory.setConfiguration(configuration);
        var session = new org.mybatis.spring.SqlSessionTemplate(factory.getObject());
        var transaction = new org.springframework.transaction.support.TransactionTemplate(
                new org.springframework.jdbc.datasource.DataSourceTransactionManager(source));
        pressure.enter(12);
        try {
            transaction.executeWithoutResult(status -> {
                assertEquals(0L, pressure.snapshot().get("started"));
                TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                    @Override public void afterCommit() { assertEquals(0, pressure.snapshot().get("active")); }
                    @Override public void afterCompletion(int status) { assertEquals(1L, pressure.snapshot().get("ended")); }
                });
                assertEquals(1, session.getMapper(OrderSql.class).update());
                assertEquals(1, session.getMapper(OrderSql.class).update());
                assertEquals(1L, pressure.snapshot().get("started"));
                assertEquals(1, pressure.snapshot().get("active"));
            });
            assertEquals(1L, pressure.snapshot().get("ended"));
            assertEquals(0, pressure.snapshot().get("active"));
            verify(connection).commit();
        } finally { pressure.leave(null); }
    }

    @Test void exactConcurrencySurvivesBriefOverlapsBetweenSamples() throws Exception {
        var pressure = new ExperimentTransactionPressure(true, () -> 1000L);
        var entered = new CountDownLatch(3);
        var release = new CountDownLatch(1);
        var executor = Executors.newFixedThreadPool(3);
        try {
            var futures = new java.util.ArrayList<java.util.concurrent.Future<?>>();
            for (int i=0;i<3;i++) futures.add(executor.submit(() -> {
                pressure.enter(12); begin();
                try {
                    pressure.beforeMapper(); entered.countDown();
                    if (!release.await(5, TimeUnit.SECONDS)) throw new AssertionError("release timed out");
                } catch (InterruptedException e) { throw new AssertionError(e); }
                finally { end(TransactionSynchronization.STATUS_COMMITTED); pressure.leave(null); }
            }));
            assertTrue(entered.await(5, TimeUnit.SECONDS));
            assertEquals(3, pressure.snapshot().get("active"));
            release.countDown();
            for (var future:futures) future.get(5, TimeUnit.SECONDS);
            assertEquals(3, pressure.snapshot().get("peakActive"));
            assertEquals(0, pressure.snapshot().get("active"));
            assertEquals(3L, pressure.snapshot().get("started"));
        } finally { release.countDown(); executor.shutdownNow(); }
    }

    @Test void retriesCountAndAnIdleNewActivityCannotInheritPreviousPeak() {
        var pressure = new ExperimentTransactionPressure(true, () -> 1000L);
        for (int i=0;i<2;i++) {
            pressure.enter(12); begin();
            try { pressure.beforeMapper(); }
            finally { end(TransactionSynchronization.STATUS_ROLLED_BACK); pressure.leave(null); }
        }
        assertEquals(2L, pressure.snapshot().get("started"));
        assertEquals(2L, rows(pressure).get(0).get("notCommitted"));
        pressure.enter(13); begin();
        try { pressure.beforeMapper(); }
        finally { end(TransactionSynchronization.STATUS_COMMITTED); pressure.leave(null); }
        assertEquals(13L, pressure.snapshot().get("activityId"));
        assertEquals(1L, pressure.snapshot().get("started"));
        assertEquals(0L, rows(pressure).get(0).get("notCommitted"));
    }

    @Test void disabledObserverAndUnrelatedTransactionsAreIgnored() {
        var pressure = new ExperimentTransactionPressure(true);
        var disabled = new ExperimentTransactionPressure(false);
        disabled.enter(12); begin();
        try { pressure.beforeMapper(); disabled.beforeMapper(); }
        finally { end(TransactionSynchronization.STATUS_COMMITTED); disabled.leave(null); }
        assertEquals(0L, pressure.snapshot().get("started"));
        assertEquals(0L, disabled.snapshot().get("started"));
    }
}
