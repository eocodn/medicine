package com.medicine.android

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

object ReminderNotifications {
    const val ACTION_TAKEN = "kr.yakbom.app.action.MEDICATION_REMINDER_TAKEN"
    const val ACTION_SKIPPED = "kr.yakbom.app.action.MEDICATION_REMINDER_SKIPPED"
    const val EXTRA_DOSE_INSTANCE_ID = "dose_instance_id"

    internal const val CHANNEL_ID = "medication_reminders_v1"
    private const val NOTIFICATION_ID = 1
    private const val GROUP_KEY = "medicine.medication.reminders"

    fun post(context: Context, reminder: ResolvedReminder, failureMessage: String? = null) {
        if (!ReminderPermissions.notificationsAllowed(context)) return
        ensureChannel(context)
        val token = reminder.key.storageToken()
        val scheduled = OffsetDateTime.parse(reminder.key.scheduledAt)
        val timeText = scheduled.format(DateTimeFormatter.ofPattern("a h:mm", Locale.KOREAN))
        val detail = listOfNotNull(
            reminder.personName,
            timeText,
            reminder.doseText?.takeIf(String::isNotBlank),
        ).joinToString(" · ")
        val contentText = failureMessage ?: detail
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification_medication)
            .setContentTitle(reminder.productName)
            .setContentText(contentText)
            .setStyle(NotificationCompat.BigTextStyle().bigText(contentText))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setGroup(GROUP_KEY)
            .setAutoCancel(true)
            .setContentIntent(contentIntent(context, token))
            .addAction(
                R.drawable.ic_notification_medication,
                "사용했어요",
                actionIntent(context, reminder, ReminderDoseStatus.TAKEN),
            )
            .addAction(
                R.drawable.ic_notification_medication,
                "건너뛰기",
                actionIntent(context, reminder, ReminderDoseStatus.SKIPPED),
            )
            .build()
        notify(context, token, notification)
        ReminderNotificationStore.remember(context, reminder, token)
    }

    fun cancel(context: Context, key: ReminderOccurrenceKey) {
        val token = key.storageToken()
        NotificationManagerCompat.from(context).cancel(token, NOTIFICATION_ID)
        ReminderNotificationStore.forgetToken(context, token)
    }

    fun cancelForDoseInstance(context: Context, doseInstanceId: String) {
        val tokens = ReminderNotificationStore.tokensForDoseInstance(context, doseInstanceId)
        val manager = NotificationManagerCompat.from(context)
        tokens.forEach { manager.cancel(it, NOTIFICATION_ID) }
        ReminderNotificationStore.forgetDoseInstance(context, doseInstanceId)
    }


    fun cancelForMedication(context: Context, medicationId: String) {
        val tokens = ReminderNotificationStore.tokensForMedication(context, medicationId)
        val manager = NotificationManagerCompat.from(context)
        tokens.forEach { manager.cancel(it, NOTIFICATION_ID) }
        ReminderNotificationStore.forgetMedication(context, medicationId)
    }

    fun cancelForPerson(context: Context, personId: String) {
        val tokens = ReminderNotificationStore.tokensForPerson(context, personId)
        val manager = NotificationManagerCompat.from(context)
        tokens.forEach { manager.cancel(it, NOTIFICATION_ID) }
        ReminderNotificationStore.forgetPerson(context, personId)
    }
    fun cancelAll(context: Context) {
        val manager = NotificationManagerCompat.from(context)
        ReminderNotificationStore.allTokens(context).forEach { manager.cancel(it, NOTIFICATION_ID) }
        ReminderNotificationStore.clear(context)
    }

    private fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            "복약 알림",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "정해진 복용 시간에 약 이름과 복용 기록 버튼을 표시합니다."
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        manager.createNotificationChannel(channel)
    }

    @Suppress("MissingPermission")
    private fun notify(context: Context, token: String, notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        NotificationManagerCompat.from(context).notify(token, NOTIFICATION_ID, notification)
    }

    private fun contentIntent(context: Context, token: String): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .setAction(Intent.ACTION_VIEW)
            .setData("yakbom://reminder/open/$token".toUri())
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    private fun actionIntent(
        context: Context,
        reminder: ResolvedReminder,
        status: ReminderDoseStatus,
    ): PendingIntent {
        val token = reminder.key.storageToken()
        val action = when (status) {
            ReminderDoseStatus.TAKEN -> ACTION_TAKEN
            ReminderDoseStatus.SKIPPED -> ACTION_SKIPPED
        }
        val intent = Intent(context, ReminderActionReceiver::class.java)
            .setAction(action)
            .setData("yakbom://reminder/action/${status.wireValue}/$token".toUri())
            .putExtra(ReminderScheduler.EXTRA_OCCURRENCE_TOKEN, token)
            .putExtra(ReminderScheduler.EXTRA_PERSON_ID, reminder.key.personId)
            .putExtra(ReminderScheduler.EXTRA_MEDICATION_ID, reminder.key.medicationId)
            .putExtra(ReminderScheduler.EXTRA_SCHEDULED_DATE, reminder.key.scheduledDate)
            .putExtra(ReminderScheduler.EXTRA_SCHEDULE_KEY, reminder.key.scheduleKey)
            .putExtra(ReminderScheduler.EXTRA_SCHEDULED_AT, reminder.key.scheduledAt)
            .putExtra(EXTRA_DOSE_INSTANCE_ID, reminder.doseInstanceId)
        return PendingIntent.getBroadcast(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}

private object ReminderNotificationStore {
    private data class Record(
        val person: String,
        val medication: String,
        val dose: String,
        val occurrence: String,
    ) {
        fun encode(): String = "$person:$medication:$dose:$occurrence"

        companion object {
            fun decode(value: String): Record? {
                val parts = value.split(':')
                if (parts.size != 4 || parts.any { it.length != 64 }) return null
                return Record(parts[0], parts[1], parts[2], parts[3])
            }
        }
    }

    fun remember(context: Context, reminder: ResolvedReminder, token: String) {
        val record = Record(
            person = ReminderIds.personToken(reminder.key.personId),
            medication = ReminderIds.medicationToken(reminder.key.medicationId),
            dose = ReminderIds.doseInstanceToken(reminder.doseInstanceId),
            occurrence = token,
        )
        val next = records(context).filterTo(linkedSetOf()) { it.occurrence != token }
        next += record
        write(context, next)
    }

    fun tokensForDoseInstance(context: Context, doseInstanceId: String): Set<String> {
        val doseToken = ReminderIds.doseInstanceToken(doseInstanceId)
        return records(context)
            .filterTo(linkedSetOf()) { it.dose == doseToken }
            .mapTo(linkedSetOf()) { it.occurrence }
    }

    fun tokensForMedication(context: Context, medicationId: String): Set<String> {
        val medicationToken = ReminderIds.medicationToken(medicationId)
        return records(context)
            .filterTo(linkedSetOf()) { it.medication == medicationToken }
            .mapTo(linkedSetOf()) { it.occurrence }
    }

    fun tokensForPerson(context: Context, personId: String): Set<String> {
        val personToken = ReminderIds.personToken(personId)
        return records(context)
            .filterTo(linkedSetOf()) { it.person == personToken }
            .mapTo(linkedSetOf()) { it.occurrence }
    }

    fun forgetDoseInstance(context: Context, doseInstanceId: String) {
        val doseToken = ReminderIds.doseInstanceToken(doseInstanceId)
        write(context, records(context).filterTo(linkedSetOf()) { it.dose != doseToken })
    }

    fun forgetMedication(context: Context, medicationId: String) {
        val medicationToken = ReminderIds.medicationToken(medicationId)
        write(context, records(context).filterTo(linkedSetOf()) { it.medication != medicationToken })
    }

    fun forgetPerson(context: Context, personId: String) {
        val personToken = ReminderIds.personToken(personId)
        write(context, records(context).filterTo(linkedSetOf()) { it.person != personToken })
    }

    fun forgetToken(context: Context, token: String) {
        write(context, records(context).filterTo(linkedSetOf()) { it.occurrence != token })
    }

    fun allTokens(context: Context): Set<String> =
        records(context).mapTo(linkedSetOf()) { it.occurrence }

    fun clear(context: Context) {
        ReminderSettings.replaceActiveNotifications(context, emptySet())
    }

    private fun records(context: Context): Set<Record> =
        ReminderSettings.activeNotifications(context).mapNotNullTo(linkedSetOf(), Record::decode)

    private fun write(context: Context, records: Set<Record>) {
        ReminderSettings.replaceActiveNotifications(context, records.mapTo(linkedSetOf(), Record::encode))
    }
}
