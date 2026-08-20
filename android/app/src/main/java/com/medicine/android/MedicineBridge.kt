package com.medicine.android

import android.util.Log
import android.webkit.JavascriptInterface
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MedicineBridge(
    referenceDatabase: File,
    personalDatabase: File,
    private val vault: PersonalDatabaseVault,
) {
    private val api: PyObject
    private val requestExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    @Volatile private var responseHandler: ((String, String) -> Unit)? = null
    private val dispatcher: BridgeRequestDispatcher

    init {
        api = PersonalDatabaseOperationCoordinator.exclusive {
            vault.openForUse()
            try {
                Python.getInstance()
                    .getModule("medicine_app.mobile_api")
                    .callAttr(
                        "create_bridge",
                        referenceDatabase.absolutePath,
                        personalDatabase.absolutePath,
                    )
                    .also { it.callAttr("prepare_for_seal") }
            } finally {
                vault.sealAfterUse()
            }
        }
        dispatcher = BridgeRequestDispatcher(
            executor = requestExecutor,
            processor = ::processRequest,
            responder = { requestId, response -> responseHandler?.invoke(requestId, response) },
        )
    }

    fun setResponseHandler(handler: (String, String) -> Unit) {
        responseHandler = handler
    }

    @JavascriptInterface
    fun requestAsync(
        requestId: String,
        method: String,
        path: String,
        body: String?,
        coalesceKey: String?,
    ) {
        dispatcher.submit(
            BridgeRequest(
                requestId = requestId,
                method = method,
                path = path,
                body = body ?: "",
                coalesceKey = coalesceKey ?: "",
            )
        )
    }

    fun close() {
        responseHandler = null
        dispatcher.close()
        // Never interrupt an in-flight personal write: it must reach the vault
        // reseal boundary even after the Activity stops accepting responses.
        requestExecutor.shutdown()
    }

    private fun processRequest(request: BridgeRequest): String {
        val access = try {
            api.callAttr("request_access", request.method, request.path).toString()
        } catch (error: Throwable) {
            Log.e(TAG, "Native API bridge access classification failed", error)
            return failureEnvelope("native bridge failure")
        }
        return when (access) {
            "reference" -> callApi(request)
            "personal_read" -> callPersonalApi(request, readOnly = true)
            "personal_write" -> callPersonalApi(request, readOnly = false)
            else -> {
                Log.e(TAG, "Native API bridge returned unsupported access class: $access")
                failureEnvelope("native bridge failure")
            }
        }
    }

    private fun callPersonalApi(request: BridgeRequest, readOnly: Boolean): String =
        PersonalDatabaseOperationCoordinator.exclusive {
            val openOrigin = try {
                vault.openForUse()
            } catch (error: Throwable) {
                Log.e(TAG, "Personal database vault open failed", error)
                return@exclusive failureEnvelope("personal data encryption failure")
            }
            var response = callApi(request)
            try {
                val discardedReadOnlySnapshot = readOnly && vault.finishReadOnlyUse(openOrigin)
                if (!discardedReadOnlySnapshot) {
                    api.callAttr("prepare_for_seal")
                    vault.sealAfterUse()
                }
            } catch (error: Throwable) {
                Log.e(TAG, "Personal database vault seal failed", error)
                response = failureEnvelope("personal data encryption failure")
            }
            response
        }

    private fun callApi(request: BridgeRequest): String = try {
        api.callAttr("request", request.method, request.path, request.body).toString()
    } catch (error: Throwable) {
        Log.e(TAG, "Native API bridge request failed", error)
        failureEnvelope("native bridge failure")
    }

    private fun failureEnvelope(detail: String): String = JSONObject()
        .put("status", 500)
        .put("body", JSONObject().put("detail", detail))
        .toString()

    companion object {
        private const val TAG = "MedicineBridge"
    }
}
