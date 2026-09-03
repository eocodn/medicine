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
            ReminderScheduler.ACTION_MAINTENANCE,
            -> ReminderScheduler.reconcileAsync(
                context,
                replaceAll = replaceAll,
                reason = intent.action ?: "system",
            )
        }
    }

}

object ReminderSystemEventPolicy {
    fun replaceAll(action: String?): Boolean = when (action) {
        Intent.ACTION_BOOT_COMPLETED,
        Intent.ACTION_MY_PACKAGE_REPLACED,
        -> true
        else -> false
    }
}
