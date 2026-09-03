package com.medicine.android

import android.content.Context


class PersonalDataRevisionSignal {
    private val listeners = linkedSetOf<(Long) -> Unit>()

    @Synchronized
    fun subscribe(listener: (Long) -> Unit) {
        listeners += listener
    }

    @Synchronized
    fun unsubscribe(listener: (Long) -> Unit) {
        listeners -= listener
    }

    fun publish(revision: Long) {
        val snapshot = synchronized(this) { listeners.toList() }
        snapshot.forEach { listener -> listener(revision) }
    }
}

object PersonalDataRevision {
    private const val PREFERENCES = "medicine.personal-data-revision"
    private const val KEY_REVISION = "revision"
    private var initialized = false
    private var processRevision = 0L
    private val externalSignal = PersonalDataRevisionSignal()

    fun current(context: Context): Long = synchronized(this) {
        ensureInitialized(context)
        processRevision
    }

    fun subscribeExternal(listener: (Long) -> Unit) = externalSignal.subscribe(listener)

    fun unsubscribeExternal(listener: (Long) -> Unit) = externalSignal.unsubscribe(listener)

    fun markChanged(context: Context): Long = synchronized(this) {
        ensureInitialized(context)
        processRevision += 1L
        // Process memory is the immediate invalidation authority. Persistence only
        // carries the revision across Activity recreation; a process restart performs
        // a full authoritative startup load regardless of this value.
        context.applicationContext
            .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_REVISION, processRevision)
            .apply()
        processRevision
    }

    fun markExternalChanged(context: Context): Long {
        val revision = markChanged(context)
        externalSignal.publish(revision)
        return revision
    }

    private fun ensureInitialized(context: Context) {
        if (initialized) return
        processRevision = context.applicationContext
            .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            .getLong(KEY_REVISION, 0L)
        initialized = true
    }
}

class PersonalDataRevisionGate(initialRevision: Long) {
    private var observedRevision = initialRevision

    @Synchronized
    fun consumeIfChanged(revision: Long): Boolean {
        if (revision == observedRevision) return false
        observedRevision = revision
        return true
    }
}
