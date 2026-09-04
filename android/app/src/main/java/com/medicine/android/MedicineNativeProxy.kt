package com.medicine.android

import android.webkit.JavascriptInterface

interface MedicineRequestDelegate {
    fun requestAsync(
        requestId: String,
        method: String,
        path: String,
        body: String?,
        coalesceKey: String?,
    )
}

class MedicineNativeProxy {
    private data class PendingRequest(
        val requestId: String,
        val method: String,
        val path: String,
        val body: String?,
        val coalesceKey: String?,
    )

    private val lock = Any()
    private var delegate: MedicineRequestDelegate? = null
    private val pending = ArrayDeque<PendingRequest>()

    fun attach(bridge: MedicineRequestDelegate) {
        synchronized(lock) {
            delegate = bridge
            while (pending.isNotEmpty()) {
                val request = pending.removeFirst()
                bridge.requestAsync(
                    request.requestId,
                    request.method,
                    request.path,
                    request.body,
                    request.coalesceKey,
                )
            }
        }
    }

    fun detach(bridge: MedicineRequestDelegate? = null) {
        synchronized(lock) {
            if (bridge == null || delegate === bridge) delegate = null
        }
    }

    @JavascriptInterface
    fun requestAsync(
        requestId: String,
        method: String,
        path: String,
        body: String?,
        coalesceKey: String?,
    ) {
        val request = PendingRequest(requestId, method, path, body, coalesceKey)
        val target = synchronized(lock) {
            delegate.also {
                if (it == null) pending.addLast(request)
            }
        }
        target?.requestAsync(requestId, method, path, body, coalesceKey)
    }
}