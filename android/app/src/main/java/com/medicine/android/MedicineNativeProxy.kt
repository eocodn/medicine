package com.medicine.android

import android.webkit.JavascriptInterface

class MedicineNativeProxy {
    @Volatile private var delegate: MedicineBridge? = null

    fun attach(bridge: MedicineBridge) {
        delegate = bridge
    }

    fun detach(bridge: MedicineBridge? = null) {
        if (bridge == null || delegate === bridge) delegate = null
    }

    @JavascriptInterface
    fun requestAsync(
        requestId: String,
        method: String,
        path: String,
        body: String?,
        coalesceKey: String?,
    ) {
        delegate?.requestAsync(requestId, method, path, body, coalesceKey)
    }
}