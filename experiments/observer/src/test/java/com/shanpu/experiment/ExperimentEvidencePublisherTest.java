package com.shanpu.experiment;

import com.alibaba.druid.pool.DruidDataSource;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.shanpu.cache.ActivityLocalCache;
import com.shanpu.common.result.Result;
import org.apache.catalina.connector.Connector;
import org.apache.catalina.startup.Tomcat;
import org.apache.coyote.AbstractProtocol;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.web.embedded.tomcat.TomcatWebServer;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import org.springframework.mock.env.MockEnvironment;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class ExperimentEvidencePublisherTest {
    @TempDir Path directory;

    @Test void completeSnapshotsArePublishedWithoutCallingBusinessHttpAndFailureKeepsLastSnapshot() throws Exception {
        var controller = mock(ExperimentMetrics.Controller.class);
        when(controller.metrics()).thenReturn(Result.ok(Map.of("timeMs", 1, "settings", Map.of("sutId", "sut"))));
        var publisher = new ExperimentEvidencePublisher(controller, new ObjectMapper(), directory.resolve("commits.jsonl").toString());
        try {
            publisher.publish();
            String snapshot = Files.readString(directory.resolve("metrics.json"));
            assertEquals(1, new ObjectMapper().readTree(snapshot).get("timeMs").asInt());
            when(controller.metrics()).thenThrow(new IllegalStateException("observation failed"));
            assertThrows(IllegalStateException.class, publisher::publish);
            assertEquals(snapshot, Files.readString(directory.resolve("metrics.json")));
        } finally { publisher.close(); }
    }

    @Test void snapshotReportsActualConnectorAndPoolValuesRatherThanOnlyYamlProperties() throws Exception {
        var context = mock(ServletWebServerApplicationContext.class);
        var server = mock(TomcatWebServer.class);
        var tomcat = new Tomcat();
        var connector = new Connector();
        tomcat.getService().addConnector(connector);
        var protocol = (AbstractProtocol<?>) connector.getProtocolHandler();
        protocol.setMaxConnections(128);
        protocol.setMaxThreads(32);
        when(context.getWebServer()).thenReturn(server);
        when(server.getTomcat()).thenReturn(tomcat);
        var journal = mock(ExperimentJournal.class);
        when(journal.snapshot()).thenReturn(Map.of("enabled", true));
        var pool = new DruidDataSource();
        pool.setMaxActive(50);
        pool.setMaxWait(60000);
        try {
            var controller = new ExperimentMetrics.Controller(new ExperimentMetrics(), journal, pool,
                    mock(ActivityLocalCache.class), new MockEnvironment().withProperty("server.tomcat.threads.max", "100"), context);
            var data = controller.metrics().getData();
            var settings = (Map<?, ?>) data.get("settings");
            assertEquals("32", settings.get("server.tomcat.threads.max"));
            assertEquals("128", settings.get("server.tomcat.max-connections"));
            assertEquals("50", settings.get("spring.datasource.druid.max-active"));
            assertTrue(data.containsKey("httpConnections"));
        } finally { pool.close(); connector.destroy(); }
    }
}
