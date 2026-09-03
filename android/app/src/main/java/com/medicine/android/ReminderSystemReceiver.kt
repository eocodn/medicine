package com.medicine.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class ReminderSystemReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val replaceAll = ReminderSystemEventPolicy.replaceAll(intent.action)
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            ACTION_EXACT_ALARM_PERMISSION_CHANGED,
            ReminderScheduler.ACTION_MAINTENANCE,
            -> ReminderScheduler.reconcileAsync(
                context,
                replaceAll = replaceAll,
                reason = intent.action ?: "system",
            )
        }
    }

    companion object {
        internal const val ACTION_EXACT_ALARM_PERMISSION_CHANGED =
            "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED"
    }
}

object ReminderSystemEventPolicy {
    fun replaceAll(action: String?): Boolean = when (action) {
        Intent.ACTION_BOOT_COMPLETED,
        Intent.ACTION_MY_PACKAGE_REPLACED,
        ReminderSystemReceiver.ACTION_EXACT_ALARM_PERMISSION_CHANGED,
        -> true
        else -> false
    }
}
