package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReminderScheduleDiffTest {
    private fun occurrence(
        medicationId: String,
        scheduleKey: String,
        scheduledAt: String,
    ) = ReminderOccurrence(
        key = ReminderOccurrenceKey(
            personId = "person-1",
            medicationId = medicationId,
            scheduledDate = scheduledAt.substring(0, 10),
            scheduleKey = scheduleKey,
            scheduledAt = scheduledAt,
        ),
    )

    @Test
    fun diffKeepsUnchangedOccurrenceAndReplacesStaleSchedule() {
        val unchanged = occurrence("med-a", "slot:1", "2026-09-04T08:00:00+09:00")
        val stale = occurrence("med-b", "slot:1", "2026-09-04T09:00:00+09:00")
        val replacement = occurrence("med-b", "slot:1", "2026-09-04T10:00:00+09:00")
        val added = occurrence("med-c", "slot:2", "2026-09-04T20:00:00+09:00")

        val diff = ReminderScheduleDiff.between(
            existingTokens = setOf(unchanged.storageToken(), stale.storageToken()),
            desired = listOf(unchanged, replacement, added),
        )

        assertEquals(setOf(stale.storageToken()), diff.cancelTokens)
        assertEquals(setOf(replacement, added), diff.schedule.toSet())
        assertTrue(unchanged !in diff.schedule)
    }

    @Test
    fun storageTokenIsStableAndDoesNotPersistMedicationIdentityInPlaintext() {
        val original = occurrence("med:/한글", "slot:2", "2026-09-04T20:00:00+09:00")
        val same = occurrence("med:/한글", "slot:2", "2026-09-04T20:00:00+09:00")
        val token = original.storageToken()
        assertEquals(token, same.storageToken())
        assertEquals(64, token.length)
        assertTrue(!token.contains("한글"))
        assertTrue(!token.contains("person-1"))
    }
}
