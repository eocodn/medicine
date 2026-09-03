package com.medicine.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import java.util.concurrent.Executors

class ReminderActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = when (intent.action) {
            ReminderNotifications.ACTION_TAKEN -> ReminderDoseStatus.TAKEN
            ReminderNotifications.ACTION_SKIPPED -> ReminderDoseStatus.SKIPPED
            else -> return
        }
        val occurrence = ReminderScheduler.occurrenceFrom(intent) ?: return
        val instanceId = intent.getStringExtra(ReminderNotifications.EXTRA_DOSE_INSTANCE_ID) ?: return
        val pending = goAsync()
        executor.execute {
            try {
                ReminderRepository(context).use { repository ->
                    try {
                        repository.recordDose(instanceId, status)
                        ReminderNotifications.cancel(context, occurrence.key)
                    } catch (error: Throwable) {
                        // A native/vault failure can be ambiguous after the Rust transaction.
                        // Re-read authoritative state before deciding whether the action failed.
                        val resolved = try {
                            repository.resolve(occurrence.key)
                        } catch (resolveError: Throwable) {
                            Log.e(TAG, "Reminder action reconciliation failed", resolveError)
                            null
                        }
                        if (resolved == null) {
                            ReminderNotifications.cancel(context, occurrence.key)
                        } else {
                            ReminderNotifications.post(
                                context,
                                resolved,
                                failureMessage = "기록하지 못했어요. 앱에서 다시 확인해주세요.",
                            )
                            Log.e(TAG, "Medication reminder action failed", error)
                        }
                    }
                }
            } finally {
                ReminderScheduler.reconcileAsync(context, reason = "notification_action")
                pending.finish()
            }
        }
    }

    companion object {
        private const val TAG = "ReminderActionReceiver"
        private val executor = Executors.newSingleThreadExecutor()
    }
}
