package com.medicine.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import java.util.concurrent.Executors

class ReminderAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ReminderScheduler.ACTION_ALARM) return
        val occurrence = ReminderScheduler.occurrenceFrom(intent) ?: return
        ReminderScheduler.markDelivered(context, occurrence)
        val pending = goAsync()
        executor.execute {
            try {
                if (!ReminderSettings.isEnabled(context) ||
                    !ReminderPermissions.notificationsAllowed(context)
                ) {
                    return@execute
                }
                val resolved = ReminderRepository(context).use { it.resolve(occurrence.key) }
                if (resolved == null) {
                    ReminderNotifications.cancel(context, occurrence.key)
                } else {
                    ReminderNotifications.post(context, resolved)
                }
            } catch (error: Throwable) {
                Log.e(TAG, "Medication reminder delivery failed", error)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        private const val TAG = "ReminderAlarmReceiver"
        private val executor = Executors.newSingleThreadExecutor()
    }
}
