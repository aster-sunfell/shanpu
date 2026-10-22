package com.shanpu.experiment;

import com.alibaba.druid.pool.DruidDataSource;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.kafka.KafkaProperties;
import org.springframework.boot.autoconfigure.web.ServerProperties;
import org.springframework.boot.autoconfigure.data.redis.RedisProperties;
import org.springframework.boot.autoconfigure.web.embedded.TomcatWebServerFactoryCustomizer;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.test.context.ConfigDataApplicationContextInitializer;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory;
import org.springframework.boot.web.embedded.tomcat.TomcatWebServer;
import org.apache.coyote.AbstractProtocol;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.net.Socket;
import java.net.InetAddress;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.URI;
import java.time.Duration;
import java.util.ArrayList;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class BusinessRuntimeAlignmentTest {
    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties({ServerProperties.class, KafkaProperties.class, RedisProperties.class})
    static class Properties {
        @Bean DruidDataSource pool(Environment environment) {
            return Binder.get(environment).bind("spring.datasource.druid", DruidDataSource.class).orElseThrow(() -> new IllegalStateException("Missing Druid config"));
        }
    }

    private ApplicationContextRunner configurationRunner(String profiles) {
        String location = Path.of("../application/src/main/resources").toAbsolutePath().normalize().toUri().toString();
        if (!location.endsWith("/")) location += "/";
        return new ApplicationContextRunner().withInitializer(new ConfigDataApplicationContextInitializer())
                .withUserConfiguration(Properties.class)
                .withPropertyValues("spring.profiles.active="+profiles, "spring.config.additional-location="+location,
                        "MYSQL_HOST=test-mysql", "MYSQL_DATABASE=test", "MYSQL_USERNAME=test", "MYSQL_PASSWORD=test",
                        "REDIS_HOST=test-redis", "REDIS_PASSWORD=test", "SUT_INSTANCE_ID=test-instance",
                        "KAFKA_BOOTSTRAP_SERVERS=test-kafka:9092");
    }

    private Map<String, Object> configuration(String profiles) {
        var result = new AtomicReference<Map<String, Object>>();
        configurationRunner(profiles)
                .run(context -> {
                    assertNull(context.getStartupFailure());
                    ServerProperties server = context.getBean(ServerProperties.class);
                    DruidDataSource pool = context.getBean(DruidDataSource.class);
                    KafkaProperties kafka = context.getBean(KafkaProperties.class);
                    Map<String, Object> values = new LinkedHashMap<>();
                    values.put("threads", server.getTomcat().getThreads().getMax());
                    values.put("spareThreads", server.getTomcat().getThreads().getMinSpare());
                    values.put("connections", server.getTomcat().getMaxConnections());
                    values.put("acceptCount", server.getTomcat().getAcceptCount());
                    values.put("connectionTimeout", server.getTomcat().getConnectionTimeout());
                    values.put("keepAliveTimeout", server.getTomcat().getKeepAliveTimeout());
                    values.put("poolInitial", pool.getInitialSize());
                    values.put("poolIdle", pool.getMinIdle());
                    values.put("poolMax", pool.getMaxActive());
                    values.put("poolWait", pool.getMaxWait());
                    values.put("producer", kafka.buildProducerProperties(null));
                    values.put("consumer", kafka.buildConsumerProperties(null));
                    var redisPool = context.getBean(RedisProperties.class).getLettuce().getPool();
                    values.put("redisPool", java.util.List.of(redisPool.getMaxActive(), redisPool.getMaxIdle(), redisPool.getMinIdle()));
                    values.put("logging", context.getEnvironment().getProperty("logging.level.com.shanpu"));
                    result.set(values);
                });
        return result.get();
    }

    @Test void experimentInheritsSharedBusiness2c2gParameters() {
        Map<String, Object> business = configuration("prod");
        Map<String, Object> experiment = configuration("prod,experiment,sut");
        assertEquals(business, experiment);
        assertEquals(business, configuration("dev"));
        assertEquals(32, experiment.get("threads"));
        assertEquals(8192, experiment.get("connections"));
        assertEquals(8, experiment.get("poolMax"));
        assertEquals(3000L, experiment.get("poolWait"));
        assertEquals(Duration.ofSeconds(5), experiment.get("keepAliveTimeout"));
        assertEquals("INFO", experiment.get("logging"));
    }

    @Test void sharedConnectorAcceptsGeneratorSocketBudgetAndStillServesAnotherRequest() {
        configurationRunner("prod,experiment,sut").run(context -> {
            var factory = new TomcatServletWebServerFactory(0);
            factory.setAddress(InetAddress.getLoopbackAddress());
            new TomcatWebServerFactoryCustomizer(context.getEnvironment(), context.getBean(ServerProperties.class)).customize(factory);
            var server = (TomcatWebServer) factory.getWebServer(servletContext -> {
                servletContext.addServlet("probe", new HttpServlet() {
                    @Override protected void doGet(HttpServletRequest request, HttpServletResponse response) throws java.io.IOException {
                        response.getWriter().write("ok");
                    }
                }).addMapping("/probe");
            });
            var sockets = new ArrayList<Socket>();
            try {
                server.start();
                for (int i = 0; i < 6400; i++) {
                    var socket = new Socket(InetAddress.getLoopbackAddress(), server.getPort());
                    sockets.add(socket);
                    // Idle sockets test admission without allocating thousands of partial HTTP parsers.
                }
                var protocol = (AbstractProtocol<?>) server.getTomcat().getConnector().getProtocolHandler();
                long deadline = System.nanoTime()+Duration.ofSeconds(2).toNanos();
                while (protocol.getConnectionCount() < 6400 && System.nanoTime() < deadline) Thread.sleep(10);
                // Tomcat reserves a slot before accept(), so its admission counter may
                // include the acceptor waiting for the next socket as well as our 6400.
                long connections = protocol.getConnectionCount();
                assertTrue(connections >= 6400 && connections <= 6401,
                        () -> "Expected 6400 sockets plus at most one acceptor reservation, got "+connections);
                var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+server.getPort()+"/probe"))
                        .timeout(Duration.ofSeconds(2)).build();
                var response = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build()
                        .send(request, HttpResponse.BodyHandlers.ofString());
                assertEquals(200, response.statusCode());
                assertEquals("ok", response.body());
            } finally {
                for (var socket : sockets) socket.close();
                server.stop();
                server.destroy();
            }
        });
    }
}
