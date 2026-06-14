package com.shanpu.module.health.controller;

import lombok.RequiredArgsConstructor;
import org.apache.kafka.clients.admin.AdminClient;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.data.redis.connection.RedisConnection;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@RestController
@RequiredArgsConstructor
public class HealthController {

    private static final String UP = "UP";
    private static final String DOWN = "DOWN";

    private final DataSource dataSource;
    private final RedisConnectionFactory redisConnectionFactory;
    private final KafkaAdmin kafkaAdmin;

    @GetMapping("/api/health")
    public Map<String, String> health() {
        String mysql = checkMysql();
        String redis = checkRedis();
        String kafka = checkKafka();
        boolean ok = UP.equals(mysql) && UP.equals(redis) && UP.equals(kafka);
        Map<String, String> result = new LinkedHashMap<>();
        result.put("status", ok ? UP : DOWN);
        result.put("mysql", mysql);
        result.put("redis", redis);
        result.put("kafka", kafka);
        return result;
    }

    private String checkMysql() {
        try (Connection conn = dataSource.getConnection();
             Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT 1")) {
            return rs.next() ? UP : DOWN;
        } catch (Exception e) {
            return DOWN;
        }
    }

    private String checkRedis() {
        try (RedisConnection conn = redisConnectionFactory.getConnection()) {
            String pong = conn.ping();
            return "PONG".equalsIgnoreCase(pong) ? UP : DOWN;
        } catch (Exception e) {
            return DOWN;
        }
    }

    private String checkKafka() {
        try (AdminClient client = AdminClient.create(kafkaAdmin.getConfigurationProperties())) {
            client.describeCluster().clusterId().get(2, TimeUnit.SECONDS);
            return UP;
        } catch (Exception e) {
            return DOWN;
        }
    }
}
