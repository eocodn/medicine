package com.medicine.android

import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.OffsetDateTime

data class ReminderOccurrenceKey(
    val personId: String,
    val medicationId: String,
    val scheduledDate: String,
    val scheduleKey: String,
    val scheduledAt: String,
) {
    fun storageToken(): String = ReminderIds.sha256Token(
        "occurrence",
        personId,
        medicationId,
        scheduledDate,
        scheduleKey,
        scheduledAt,
    )
}

data class ReminderOccurrence(
    val key: ReminderOccurrenceKey,
) {
    fun storageToken(): String = key.storageToken()

    fun triggerAtMillis(): Long = OffsetDateTime.parse(key.scheduledAt).toInstant().toEpochMilli()
}

data class ReminderScheduleDiff(
    val cancelTokens: Set<String>,
    val schedule: List<ReminderOccurrence>,
) {
    companion object {
        fun between(
            existingTokens: Set<String>,
            desired: List<ReminderOccurrence>,
        ): ReminderScheduleDiff {
            val desiredTokens = desired.associateBy(ReminderOccurrence::storageToken)
            return ReminderScheduleDiff(
                cancelTokens = existingTokens - desiredTokens.keys,
                schedule = desired.filter { it.storageToken() !in existingTokens },
            )
        }
    }
}

data class ResolvedReminder(
    val key: ReminderOccurrenceKey,
    val doseInstanceId: String,
    val personName: String,
    val productName: String,
    val doseText: String?,
)

enum class ReminderDoseStatus(val wireValue: String) {
    TAKEN("taken"),
    SKIPPED("skipped"),
}

object ReminderIds {
    fun personToken(personId: String): String = sha256Token("person", personId)

    fun medicationToken(medicationId: String): String = sha256Token("medication", medicationId)

    fun doseInstanceToken(instanceId: String): String = sha256Token("dose-instance", instanceId)

    fun sha256Token(namespace: String, vararg values: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        fun add(value: String) {
            val bytes = value.toByteArray(StandardCharsets.UTF_8)
            digest.update(bytes.size.toString().toByteArray(StandardCharsets.US_ASCII))
            digest.update(':'.code.toByte())
            digest.update(bytes)
            digest.update(';'.code.toByte())
        }
        add(namespace)
        values.forEach(::add)
        return digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }
}
