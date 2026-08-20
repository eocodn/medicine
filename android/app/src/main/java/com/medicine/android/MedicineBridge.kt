package com.medicine.android

import android.util.Log
import android.webkit.JavascriptInterface
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import org.json.JSONObject
import java.io.File

class MedicineBridge(
    referenceDatabase: File,
    personalDatabase: File,
    private val vault: PersonalDatabaseVault,
) {
    private val lock = Any()
    private val api: PyObject

    init {
        vault.openForUse()
        try {
            api = Python.getInstance()
                .getModule("medicine_app.mobile_api")
                .callAttr(
                    "create_bridge",
                    referenceDatabase.absolutePath,
                    personalDatabase.absolutePath,
                )
            api.callAttr("prepare_for_seal")
        } finally {
            vault.sealAfterUse()
        }
    }

    fun setReferenceAvailable(available: Boolean, reason: String? = null) = synchronized(lock) {
        api.callAttr("set_reference_available", available, reason)
    }

    @JavascriptInterface
    fun request(method: String, path: String, body: String?): String = synchronized(lock) {
        try {
            vault.openForUse()
        } catch (error: Throwable) {
            Log.e(TAG, "Personal database vault open failed", error)
            return@synchronized JSONObject()
                .put("status", 500)
                .put("body", JSONObject().put("detail", "personal data encryption failure"))
                .toString()
        }
        var response: String
        try {
            response = api.callAttr("request", method, path, body ?: "").toString()
        } catch (error: Throwable) {
            Log.e(TAG, "Native API bridge request failed", error)
            response = JSONObject()
                .put("status", 500)
                .put("body", JSONObject().put("detail", "native bridge failure"))
                .toString()
        }
        try {
            api.callAttr("prepare_for_seal")
            vault.sealAfterUse()
        } catch (error: Throwable) {
            Log.e(TAG, "Personal database vault seal failed", error)
            response = JSONObject()
                .put("status", 500)
                .put("body", JSONObject().put("detail", "personal data encryption failure"))
                .toString()
        }
        response
    }

    companion object {
        private const val TAG = "MedicineBridge"
    }
}
