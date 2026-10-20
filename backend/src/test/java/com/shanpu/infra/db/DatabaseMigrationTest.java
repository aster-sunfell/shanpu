package com.shanpu.infra.db;

import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.FlywayException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ScriptUtils;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

@Testcontainers
class DatabaseMigrationTest {
    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.0")
            .withDatabaseName("migration_test");

    private DriverManagerDataSource dataSource;
    private JdbcTemplate jdbc;

    private Flyway flyway(String... locations) {
        return Flyway.configure().dataSource(dataSource).locations(locations)
                .baselineOnMigrate(false).cleanDisabled(true).load();
    }

    @BeforeEach
    void resetIsolatedDatabase() {
        dataSource = new DriverManagerDataSource(MYSQL.getJdbcUrl(), MYSQL.getUsername(), MYSQL.getPassword());
        jdbc = new JdbcTemplate(dataSource);
        // This datasource belongs exclusively to the disposable Testcontainers database.
        Flyway.configure().dataSource(dataSource).cleanDisabled(false).load().clean();
    }

    @Test
    void emptyDatabaseMigratesOnceWithoutDemoData() {
        Flyway migration = flyway("classpath:db/migration");
        assertEquals(1, migration.migrate().migrationsExecuted);
        assertEquals(0, migration.migrate().migrationsExecuted);
        assertEquals(6, jdbc.queryForObject("SELECT COUNT(*) FROM information_schema.tables "
                + "WHERE table_schema = DATABASE() AND table_name LIKE 't_%'", Integer.class));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM t_user", Integer.class));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM t_product", Integer.class));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM flyway_schema_history WHERE success = 1", Integer.class));
        assertEquals("migration_test", jdbc.queryForObject("SELECT DATABASE()", String.class));
    }

    @Test
    void existingSchemaRequiresExplicitBaselineAndPreservesData() throws Exception {
        try (var connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection, new ClassPathResource("db/migration/V1__initial_schema.sql"));
        }
        jdbc.update("INSERT INTO t_user(username, password, points) VALUES ('existing', 'hash', 42)");
        Flyway migration = flyway("classpath:db/migration");
        assertThrows(FlywayException.class, migration::migrate);
        migration.baseline();
        assertEquals(0, migration.migrate().migrationsExecuted);
        assertEquals(42, jdbc.queryForObject("SELECT points FROM t_user WHERE username = 'existing'", Integer.class));
    }

    @Test
    void changedMigrationFailsValidation(@TempDir Path directory) throws Exception {
        flyway("classpath:db/migration").migrate();
        String sql = new ClassPathResource("db/migration/V1__initial_schema.sql")
                .getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        Files.writeString(directory.resolve("V1__initial_schema.sql"), sql.replace("VARCHAR(32)", "VARCHAR(33)"));
        assertThrows(FlywayException.class, () -> flyway("filesystem:" + directory).migrate());
    }
}
