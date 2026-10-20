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
    @Test
    void cursorPagesAreBoundedFilteredAndNeverReread() throws Exception {
        Path file = directory.resolve("paged.jsonl");
        try (var journal = new JournalFixture(file)) {
            for (int i = 0; i < 1005; i++) journal.value.recordCommittedOrder(12L, 34L, "o" + i);
            journal.value.recordCommittedOrder(99L, 34L, "other");
            var first = journal.value.readPage(0, 12);
            assertEquals(1000, ((java.util.List<?>) first.get("events")).size());
            assertEquals(true, first.get("hasMore"));
            var second = journal.value.readPage((Long) first.get("nextOffset"), 12);
            assertEquals(5, ((java.util.List<?>) second.get("events")).size());
            assertEquals(false, second.get("hasMore"));
            assertEquals(Files.size(file), second.get("nextOffset"));
            assertTrue(((java.util.List<?>) journal.value.readPage((Long) second.get("nextOffset"), 12).get("events")).isEmpty());
            assertThrows(IllegalArgumentException.class, () -> journal.value.readPage(-1, 12));
            assertThrows(IllegalArgumentException.class, () -> journal.value.readPage(Files.size(file) + 1, 12));
        }
    }

    @Test
    void incompleteTrailingRecordDoesNotAdvanceTheCursor() throws Exception {
        Path file = directory.resolve("partial.jsonl");
        Files.writeString(file, "{\"activityId\":12");
        var journal = new ExperimentJournal(true, file.toString(), new ObjectMapper());
        var page = journal.readPage(0, 12);
        assertEquals(0L, page.get("nextOffset"));
        assertEquals(true, page.get("hasMore"));
        assertTrue(((java.util.List<?>) page.get("events")).isEmpty());
    }

    private static class JournalFixture implements AutoCloseable {
        final ExperimentJournal value;
        JournalFixture(Path path) { value = new ExperimentJournal(true, path.toString(), new ObjectMapper()); }
        @Override public void close() throws Exception { value.close(); }
    }

}
