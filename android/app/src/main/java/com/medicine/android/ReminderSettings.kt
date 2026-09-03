package com.medicine.android

import android.content.Context
import androidx.core.content.edit

object ReminderSettings {
    private const val PREFS = "medicine.reminders"
    private const val ENABLED = "enabled"
    private const val AUTO_OFFER_COMPLETED = "auto_offer_completed"
    private const val EXPLICITLY_DISABLED = "explicitly_disabled"
    private const val SCHEDULED_TOKENS = "scheduled_tokens"
    private const val ACTIVE_NOTIFICATIONS = "active_notifications"

    fun isEnabled(context: Context): Boolean = prefs(context).getBoolean(ENABLED, false)

    fun enableFromAutomaticOffer(context: Context) {
        prefs(context).edit {
            putBoolean(ENABLED, true)
            putBoolean(AUTO_OFFER_COMPLETED, true)
            putBoolean(EXPLICITLY_DISABLED, false)
        }
    }

    fun setEnabledByUser(context: Context, enabled: Boolean) {
        prefs(context).edit {
            putBoolean(ENABLED, enabled)
            putBoolean(EXPLICITLY_DISABLED, !enabled)
        }
    }

    fun shouldAutomaticallyOffer(context: Context): Boolean {
        val preferences = prefs(context)
        return !preferences.getBoolean(AUTO_OFFER_COMPLETED, false) &&
            !preferences.getBoolean(EXPLICITLY_DISABLED, false)
    }

    fun markAutomaticOfferCompleted(context: Context) {
        prefs(context).edit { putBoolean(AUTO_OFFER_COMPLETED, true) }
    }

    fun scheduledTokens(context: Context): Set<String> =
        prefs(context).getStringSet(SCHEDULED_TOKENS, emptySet())?.toSet() ?: emptySet()

    fun replaceScheduledTokens(context: Context, tokens: Set<String>) {
        prefs(context).edit { putStringSet(SCHEDULED_TOKENS, tokens.toSet()) }
    }

    fun removeScheduledToken(context: Context, token: String) {
        val next = scheduledTokens(context).toMutableSet()
        if (next.remove(token)) replaceScheduledTokens(context, next)
    }

    fun activeNotifications(context: Context): Set<String> =
        prefs(context).getStringSet(ACTIVE_NOTIFICATIONS, emptySet())?.toSet() ?: emptySet()

    fun replaceActiveNotifications(context: Context, records: Set<String>) {
        prefs(context).edit { putStringSet(ACTIVE_NOTIFICATIONS, records.toSet()) }
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
