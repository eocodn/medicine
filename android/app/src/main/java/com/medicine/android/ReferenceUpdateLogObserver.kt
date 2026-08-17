package com.medicine.android

import android.util.Log

class ReferenceUpdateLogObserver(
    private val tag: String = "ReferenceUpdater",
) : ReferenceUpdateObserver {
    private var lastPercent = -1

    override fun phase(name: String) {
        lastPercent = -1
        Log.i(tag, "reference update phase=$name")
    }

    override fun progress(name: String, completedBytes: Long, totalBytes: Long) {
        if (totalBytes <= 0) return
        val percent = ((completedBytes * 100) / totalBytes).toInt().coerceIn(0, 100)
        if (percent == 100 || lastPercent < 0 || percent >= lastPercent + 5) {
            lastPercent = percent
            Log.i(tag, "reference update progress=$name $percent% ($completedBytes/$totalBytes)")
        }
    }
}
