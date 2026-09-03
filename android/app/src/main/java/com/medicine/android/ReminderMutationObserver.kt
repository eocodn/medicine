package com.medicine.android

import android.content.Context

object ReminderMutationObserver {
    fun onCommitted(context: Context, request: BridgeRequest) {
        val appContext = context.applicationContext
        val method = request.method.trim().uppercase()
        val path = request.path.substringBefore('?').substringBefore('#')
        when {
            method == "POST" -> {
                doseInstanceId(path)?.let {
                    ReminderNotifications.cancelForDoseInstance(appContext, it)
                }
            }
            method == "PATCH" || method == "DELETE" -> {
                medicationId(path)?.let {
                    ReminderNotifications.cancelForMedication(appContext, it)
                }
                if (method == "DELETE") {
                    personId(path)?.let {
                        ReminderNotifications.cancelForPerson(appContext, it)
                    }
                }
            }
        }
        ReminderScheduler.reconcileAsync(appContext, reason = "personal_write")
    }

    private fun doseInstanceId(path: String): String? =
        singleSegment(path, "/api/dose-instances/")

    private fun medicationId(path: String): String? =
        singleSegment(path, "/api/medications/")

    private fun personId(path: String): String? =
        singleSegment(path, "/api/people/")

    private fun singleSegment(path: String, prefix: String): String? {
        val rest = path.removePrefix(prefix)
        if (rest == path || rest.isEmpty() || '/' in rest) return null
        return rest
    }
}
