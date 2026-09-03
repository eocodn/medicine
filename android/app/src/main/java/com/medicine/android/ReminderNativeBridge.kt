package com.medicine.android

import android.content.Context
import android.webkit.JavascriptInterface
import org.json.JSONObject

class ReminderNativeBridge(
    context: Context,
    private val requestNotificationPermission: () -> Unit,
    private val onStatusChanged: () -> Unit,
) {
    private val appContext = context.applicationContext
    @Volatile private var pendingEnable: PendingEnable? = null

    @JavascriptInterface
    fun status(): String = JSONObject()
        .put("supported", true)
        .put("enabled", ReminderSettings.isEnabled(appContext))
        .put("notifications_allowed", ReminderPermissions.notificationsAllowed(appContext))
        .toString()

    @JavascriptInterface
    fun setEnabled(enabled: Boolean) {
        if (!enabled) {
            pendingEnable = null
            ReminderSettings.setEnabledByUser(appContext, false)
            ReminderScheduler.reconcileAsync(appContext, reason = "user_disabled")
            onStatusChanged()
            return
        }
        requestEnable(PendingEnable.USER)
    }

    @JavascriptInterface
    fun offerAfterScheduledMedicationSave() {
        if (ReminderSettings.isEnabled(appContext)) {
            ReminderScheduler.reconcileAsync(appContext, reason = "scheduled_medication_saved")
            onStatusChanged()
            return
        }
        if (!ReminderSettings.shouldAutomaticallyOffer(appContext)) return
        ReminderSettings.markAutomaticOfferCompleted(appContext)
        requestEnable(PendingEnable.AUTOMATIC_OFFER)
    }

    fun onNotificationPermissionResult(granted: Boolean) {
        val request = pendingEnable ?: return
        pendingEnable = null
        if (granted && ReminderPermissions.notificationsAllowed(appContext)) {
            enable(request)
        } else {
            onStatusChanged()
        }
    }

    fun refresh() {
        val pending = pendingEnable
        if (pending != null && ReminderPermissions.notificationsAllowed(appContext)) {
            pendingEnable = null
            enable(pending)
            return
        }
        if (ReminderSettings.isEnabled(appContext)) {
            ReminderScheduler.reconcileAsync(
                appContext,
                replaceAll = true,
                reason = "activity_resume",
            )
        }
        onStatusChanged()
    }

    private fun requestEnable(request: PendingEnable) {
        if (ReminderPermissions.notificationsAllowed(appContext)) {
            enable(request)
            return
        }
        pendingEnable = request
        requestNotificationPermission()
    }

    private fun enable(request: PendingEnable) {
        when (request) {
            PendingEnable.USER -> ReminderSettings.setEnabledByUser(appContext, true)
            PendingEnable.AUTOMATIC_OFFER -> ReminderSettings.enableFromAutomaticOffer(appContext)
        }
        ReminderScheduler.reconcileAsync(appContext, reason = "enabled")
        onStatusChanged()
    }

    private enum class PendingEnable {
        USER,
        AUTOMATIC_OFFER,
    }
}
