package com.medicine.android

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.net.toUri
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.concurrent.Executors

object ReminderScheduler {
    const val ACTION_ALARM = "kr.yakbom.app.action.MEDICATION_REMINDER_ALARM"
    const val ACTION_MAINTENANCE = "kr.yakbom.app.action.MEDICATION_REMINDER_MAINTENANCE"
    const val EXTRA_OCCURRENCE_TOKEN = "occurrence_token"
    const val EXTRA_PERSON_ID = "person_id"
    const val EXTRA_MEDICATION_ID = "medication_id"
    const val EXTRA_SCHEDULED_DATE = "scheduled_date"
    const val EXTRA_SCHEDULE_KEY = "schedule_key"
    const val EXTRA_SCHEDULED_AT = "scheduled_at"

    private const val LOOKAHEAD_DAYS = 8
    private const val MAINTENANCE_INTERVAL_MILLIS = 24L * 60L * 60L * 1000L
    private val executor = Executors.newSingleThreadExecutor()

    fun reconcileAsync(
        context: Context,
        replaceAll: Boolean = false,
        reason: String = "state_change",
    ) {
        val appContext = context.applicationContext
        executor.execute {
            try {
                reconcile(appContext, replaceAll)
            } catch (error: Throwable) {
                Log.e(TAG, "Reminder reconciliation failed: $reason", error)
            }
        }
    }

    fun reconcile(
        context: Context,
        replaceAll: Boolean = false,
        now: OffsetDateTime = OffsetDateTime.now(KST),
    ) {
        val appContext = context.applicationContext
        if (!ReminderSettings.isEnabled(appContext) ||
            !ReminderPermissions.notificationsAllowed(appContext)
        ) {
            cancelAll(appContext)
            return
        }

        val desired = ReminderRepository(appContext).use {
            it.listUpcoming(now, LOOKAHEAD_DAYS)
        }
        val existing = ReminderSettings.scheduledTokens(appContext)
        val diff = if (replaceAll) {
            ReminderScheduleDiff(existing, desired)
        } else {
            ReminderScheduleDiff.between(existing, desired)
        }

        diff.cancelTokens.forEach { cancelToken(appContext, it) }
        diff.schedule.forEach { scheduleOccurrence(appContext, it) }
        ReminderSettings.replaceScheduledTokens(
            appContext,
            desired.mapTo(linkedSetOf(), ReminderOccurrence::storageToken),
        )
        ensureMaintenanceAlarm(appContext, now)
    }

    fun markDelivered(context: Context, occurrence: ReminderOccurrence) {
        ReminderSettings.removeScheduledToken(context, occurrence.storageToken())
    }

    fun cancelAll(context: Context) {
        val appContext = context.applicationContext
        ReminderSettings.scheduledTokens(appContext).forEach { cancelToken(appContext, it) }
        ReminderSettings.replaceScheduledTokens(appContext, emptySet())
        cancelMaintenanceAlarm(appContext)
        ReminderNotifications.cancelAll(appContext)
    }

    private fun scheduleOccurrence(context: Context, occurrence: ReminderOccurrence) {
        val alarmManager = context.getSystemService(AlarmManager::class.java)
        val pendingIntent = alarmPendingIntent(context, occurrence)
        val triggerAt = occurrence.triggerAtMillis()
        alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAt, pendingIntent)
    }

    private fun cancelToken(context: Context, token: String) {
        val alarmManager = context.getSystemService(AlarmManager::class.java)
        val intent = Intent(context, ReminderAlarmReceiver::class.java)
            .setAction(ACTION_ALARM)
            .setData(alarmUri(token))
        val pendingIntent = PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
        ) ?: return
        alarmManager.cancel(pendingIntent)
        pendingIntent.cancel()
    }

    private fun alarmPendingIntent(context: Context, occurrence: ReminderOccurrence): PendingIntent {
        val key = occurrence.key
        val token = occurrence.storageToken()
        val intent = Intent(context, ReminderAlarmReceiver::class.java)
            .setAction(ACTION_ALARM)
            .setData(alarmUri(token))
            .putExtra(EXTRA_OCCURRENCE_TOKEN, token)
            .putExtra(EXTRA_PERSON_ID, key.personId)
            .putExtra(EXTRA_MEDICATION_ID, key.medicationId)
            .putExtra(EXTRA_SCHEDULED_DATE, key.scheduledDate)
            .putExtra(EXTRA_SCHEDULE_KEY, key.scheduleKey)
            .putExtra(EXTRA_SCHEDULED_AT, key.scheduledAt)
        return PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    fun occurrenceFrom(intent: Intent): ReminderOccurrence? {
        val personId = intent.getStringExtra(EXTRA_PERSON_ID) ?: return null
        val medicationId = intent.getStringExtra(EXTRA_MEDICATION_ID) ?: return null
        val scheduledDate = intent.getStringExtra(EXTRA_SCHEDULED_DATE) ?: return null
        val scheduleKey = intent.getStringExtra(EXTRA_SCHEDULE_KEY) ?: return null
        val scheduledAt = intent.getStringExtra(EXTRA_SCHEDULED_AT) ?: return null
        val occurrence = ReminderOccurrence(
            ReminderOccurrenceKey(
                personId = personId,
                medicationId = medicationId,
                scheduledDate = scheduledDate,
                scheduleKey = scheduleKey,
                scheduledAt = scheduledAt,
            )
        )
        val suppliedToken = intent.getStringExtra(EXTRA_OCCURRENCE_TOKEN) ?: return null
        return occurrence.takeIf { it.storageToken() == suppliedToken }
    }

    private fun ensureMaintenanceAlarm(context: Context, now: OffsetDateTime) {
        val alarmManager = context.getSystemService(AlarmManager::class.java)
        val intent = Intent(context, ReminderSystemReceiver::class.java)
            .setAction(ACTION_MAINTENANCE)
            .setData("yakbom://reminder/maintenance".toUri())
        val pendingIntent = PendingIntent.getBroadcast(
            context,
            1,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val first = now.plusHours(12).toInstant().toEpochMilli()
        alarmManager.setInexactRepeating(
            AlarmManager.RTC_WAKEUP,
            first,
            MAINTENANCE_INTERVAL_MILLIS,
            pendingIntent,
        )
    }

    private fun cancelMaintenanceAlarm(context: Context) {
        val alarmManager = context.getSystemService(AlarmManager::class.java)
        val intent = Intent(context, ReminderSystemReceiver::class.java)
            .setAction(ACTION_MAINTENANCE)
            .setData("yakbom://reminder/maintenance".toUri())
        val pendingIntent = PendingIntent.getBroadcast(
            context,
            1,
            intent,
            PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE,
        ) ?: return
        alarmManager.cancel(pendingIntent)
        pendingIntent.cancel()
    }

    private fun alarmUri(token: String) = "yakbom://reminder/alarm/$token".toUri()

    private val KST: ZoneOffset = ZoneOffset.ofHours(9)
    private const val TAG = "ReminderScheduler"
}
