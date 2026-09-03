package com.medicine.android

import android.content.Context
import java.io.File
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.time.OffsetDateTime
import org.json.JSONObject

class ReminderRepository(context: Context) : AutoCloseable {
    private val appContext = context.applicationContext
    private val personalDatabase = File(appContext.filesDir, "personal.sqlite")
    private val personalApi = PersonalDatabaseApi(
        referenceDatabase = null,
        personalDatabase = personalDatabase,
        vault = PersonalDatabaseVault(
            personalDatabase,
            File(appContext.filesDir, "personal.sqlite.enc"),
            PersonalDatabaseKeyStore::getOrCreate,
        ),
    )

    fun listUpcoming(from: OffsetDateTime, days: Int): List<ReminderOccurrence> {
        val encodedFrom = URLEncoder.encode(from.toString(), StandardCharsets.UTF_8.name())
        val body = request(
            "GET",
            "/api/reminders/upcoming?from=$encodedFrom&days=$days",
            "",
        )
        val rows = body.getJSONArray("occurrences")
        return buildList(rows.length()) {
            for (index in 0 until rows.length()) {
                val item = rows.getJSONObject(index)
                add(
                    ReminderOccurrence(
                        ReminderOccurrenceKey(
                            personId = item.getString("person_id"),
                            medicationId = item.getString("medication_id"),
                            scheduledDate = item.getString("scheduled_date"),
                            scheduleKey = item.getString("schedule_key"),
                            scheduledAt = item.getString("scheduled_at"),
                        )
                    )
                )
            }
        }
    }

    fun resolve(key: ReminderOccurrenceKey): ResolvedReminder? {
        val payload = JSONObject()
            .put("person_id", key.personId)
            .put("medication_id", key.medicationId)
            .put("scheduled_date", key.scheduledDate)
            .put("schedule_key", key.scheduleKey)
            .put("scheduled_at", key.scheduledAt)
        val body = request("POST", "/api/reminders/resolve", payload.toString())
        if (!body.optBoolean("active", false)) return null
        return ResolvedReminder(
            key = key,
            doseInstanceId = body.getString("dose_instance_id"),
            personName = body.getString("person_name"),
            productName = body.getString("product_name"),
            doseText = if (body.isNull("dose_text")) {
                null
            } else {
                body.optString("dose_text").takeIf { it.isNotEmpty() }
            },
        )
    }

    fun recordDose(instanceId: String, status: ReminderDoseStatus) {
        require('/' !in instanceId) { "dose instance ID cannot contain /" }
        request(
            "POST",
            "/api/dose-instances/$instanceId",
            JSONObject().put("status", status.wireValue).toString(),
        )
    }

    override fun close() {
        personalApi.close()
    }

    private fun request(method: String, path: String, body: String): JSONObject {
        val envelope = try {
            JSONObject(personalApi.request(method, path, body))
        } catch (error: Throwable) {
            throw ReminderApiException("invalid native reminder response", error)
        }
        val status = envelope.optInt("status", 500)
        if (status !in 200..299) {
            val detail = envelope.optJSONObject("body")?.optString("detail")
                ?.takeIf { it.isNotBlank() }
                ?: "reminder request failed"
            throw ReminderApiException(detail, status = status)
        }
        return envelope.optJSONObject("body") ?: JSONObject()
    }
}

class ReminderApiException(
    message: String,
    cause: Throwable? = null,
    val status: Int? = null,
) : RuntimeException(message, cause)
