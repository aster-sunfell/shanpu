package com.shanpu.module.order;

import com.shanpu.common.exception.BusinessException;
import com.shanpu.module.order.service.OrderService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.jdbc.Sql;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 并发防超卖集成测试：使用独立测试商品，50 线程持续并发下单，
 * 断言最终 PAID 订单数 = 库存、库存归零、扣分流水数 = 成功订单数、无超卖。
 * 经真实 MySQL 容器运行 Flyway 迁移，验证行锁事务在真实引擎下的正确性。
 */
@Testcontainers
@ActiveProfiles("test")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@Sql("/fixtures/order-concurrency.sql")
class OrderConcurrencyTest {

    private static final long PRODUCT_ID = 5L;
    private static final int STOCK = 10;
    private static final int THREADS = 50;
    private static final Duration RUN = Duration.ofSeconds(10);

    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>(DockerImageName.parse("mysql:8.0"))
            .withDatabaseName("order_concurrency_test");

    @DynamicPropertySource
    static void datasourceProps(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", MYSQL::getJdbcUrl);
        registry.add("spring.datasource.username", MYSQL::getUsername);
        registry.add("spring.datasource.password", MYSQL::getPassword);
    }

    @Autowired
    private OrderService orderService;

    @Autowired
    private JdbcTemplate jdbc;

    @BeforeEach
    void resetFixture() {
        jdbc.update("DELETE FROM t_point_log WHERE biz_ref IN "
                + "(SELECT order_id FROM t_order WHERE product_id = ?)", PRODUCT_ID);
        jdbc.update("DELETE FROM t_order WHERE product_id = ?", PRODUCT_ID);
        jdbc.update("UPDATE t_product SET stock = ? WHERE id = ?", STOCK, PRODUCT_ID);
        jdbc.update("DELETE FROM t_user");
        for (int i = 1; i <= THREADS; i++) {
            jdbc.update("INSERT INTO t_user (id, username, password, nickname, points) VALUES (?, ?, ?, '', ?)",
                    i, "u" + i, "x", 1_000_000);
        }
    }

    @Test
    void fiftyConcurrent_noOversell() throws InterruptedException {
        ExecutorService pool = Executors.newFixedThreadPool(THREADS);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(THREADS);
        AtomicInteger success = new AtomicInteger();
        long deadline = System.nanoTime() + RUN.toNanos();

        for (int t = 1; t <= THREADS; t++) {
            final long userId = t;
            pool.submit(() -> {
                try {
                    start.await();
                    while (System.nanoTime() < deadline) {
                        try {
                            orderService.createNormalOrder(userId, PRODUCT_ID, 1);
                            success.incrementAndGet();
                        } catch (BusinessException ignored) {
                            // 售罄后预期持续返回 STOCK_SHORTAGE，不计为错误
                        }
                        Thread.sleep(1);
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                } finally {
                    done.countDown();
                }
            });
        }

        start.countDown();
        assertTrue(done.await(RUN.toSeconds() + 30, TimeUnit.SECONDS), "并发任务未在期限内完成");
        pool.shutdownNow();

        Integer paid = jdbc.queryForObject(
                "SELECT COUNT(*) FROM t_order WHERE product_id = ? AND source = 1 AND status = 2",
                Integer.class, PRODUCT_ID);
        Integer stock = jdbc.queryForObject(
                "SELECT stock FROM t_product WHERE id = ?", Integer.class, PRODUCT_ID);
        Integer logs = jdbc.queryForObject(
                "SELECT COUNT(*) FROM t_point_log WHERE biz_type = 2 AND biz_ref IN "
                        + "(SELECT order_id FROM t_order WHERE product_id = ?)",
                Integer.class, PRODUCT_ID);

        assertEquals(STOCK, success.get(), "成功下单次数应等于库存");
        assertEquals(STOCK, paid, "PAID 订单数应等于库存，无超卖");
        assertEquals(0, stock, "库存应归零");
        assertEquals(STOCK, logs, "扣分流水数应等于成功订单数");
    }
}
