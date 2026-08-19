package com.medicine.android

internal class BackDispatchGate {
    private var pending = false

    fun tryBegin(): Boolean {
        if (pending) return false
        pending = true
        return true
    }

    /** Returns true only when the Activity should perform its ordinary Back action. */
    fun complete(javascriptResult: String?, activityEnding: Boolean): Boolean {
        check(pending) { "Back dispatch completed without a pending JavaScript decision" }
        pending = false
        return javascriptResult != "true" && !activityEnding
    }
}
