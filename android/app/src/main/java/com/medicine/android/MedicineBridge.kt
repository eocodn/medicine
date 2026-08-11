package com.medicine.android

import android.util.Log
import android.webkit.JavascriptInterface
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import org.json.JSONObject
import java.io.File

class MedicineBridge(referenceDatabase: File, personalDatabase: File) {
    private val lock = Any()
    private val api: PyObject = Python.getInstance()
        .getModule("medicine_app.mobile_api")
        .callAttr(
            "create_bridge",
            referenceDatabase.absolutePath,
            personalDatabase.absolutePath,
            referenceDatabase.absolutePath,
        )

    @JavascriptInterface
    fun request(method: String, path: String, body: String?): String = synchronized(lock) {
        try {
            api.callAttr("request", method, path, body ?: "").toString()
        } catch (error: Throwable) {
            Log.e(TAG, "Native API bridge request failed", error)
            JSONObject()
                .put("status", 500)
                .put("body", JSONObject().put("detail", "native bridge failure"))
                .toString()
        }
    }

    companion object {
        private const val TAG = "MedicineBridge"
    }
}
