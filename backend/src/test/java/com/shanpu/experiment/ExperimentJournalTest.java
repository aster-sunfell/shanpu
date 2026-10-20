package com.shanpu.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class ExperimentJournalTest {
    @TempDir Path directory;

    @Test
    void recordsCommittedOrderInExistingEvidenceFormat() throws Exception {
        Path file = directory.resolve("commits.jsonl");
        ExperimentJournal journal = new ExperimentJournal(true, file.toString(), new ObjectMapper());
        journal.recordCommittedOrder(12L, 34L, "56");
        var event = new ObjectMapper().readTree(Files.readString(file));
        assertEquals("56", event.get("orderId").asText());
        assertEquals(12, event.get("activityId").asInt());
        assertEquals(34, event.get("userId").asInt());
        assertTrue(event.get("commitObservedAtMs").asLong() > 0);
        assertEquals(1L, journal.snapshot().get("events"));
        journal.close();
    }

    @Test
    void journalFailureIsCountedWithoutPropagatingToCaller() throws Exception {
        Path parentIsFile = directory.resolve("file");
        Files.writeString(parentIsFile, "occupied");
        ExperimentJournal journal = new ExperimentJournal(true, parentIsFile.resolve("commits.jsonl").toString(), new ObjectMapper());
        assertDoesNotThrow(() -> journal.recordCommittedOrder(1L, 2L, "3"));
        assertEquals(1L, journal.snapshot().get("writeErrors"));
        assertEquals(0L, journal.snapshot().get("events"));
    }

    @Test
    void disabledObserverHasNoSideEffects() {
        Path file = directory.resolve("disabled.jsonl");
        ExperimentJournal journal = new ExperimentJournal(false, file.toString(), new ObjectMapper());
        journal.recordCommittedOrder(1L, 2L, "3");
        journal.attempt(false);
        assertFalse(Files.exists(file));
        assertEquals(0L, journal.snapshot().get("syncAttempts"));
        assertEquals(0L, journal.snapshot().get("writeErrors"));
    }
}
