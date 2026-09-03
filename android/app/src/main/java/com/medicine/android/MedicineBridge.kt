package com.medicine.android

import android.webkit.JavascriptInterface
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import org.json.JSONObject

class MedicineBridge(
    referenceDatabase: File?,
    personalDatabase: File,
    vault: PersonalDatabaseVault,
    private val onPersonalWriteCommitted: ((BridgeRequest, String) -> Unit)? = null,
) {
    private val personalApi = PersonalDatabaseApi(referenceDatabase, personalDatabase, vault)
    private val requestExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    @Volatile private var responseHandler: ((String, String) -> Unit)? = null
    private val dispatcher = BridgeRequestDispatcher(
        executor = requestExecutor,
        processor = ::processRequest,
        responder = { requestId, response -> responseHandler?.invoke(requestId, response) },
    )
    private val closeLock = Any()
    private var closed = false

    fun setResponseHandler(handler: (String, String) -> Unit) {
        responseHandler = handler
    }

    fun setReferenceAvailable(available: Boolean, reason: String? = null) {
        personalApi.setReferenceAvailable(available, reason)
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
        synchronized(closeLock) {
            if (closed) return
            closed = true
            responseHandler = null
            dispatcher.close()
            // Queue native teardown after the dispatch drain. This preserves the
            // invariant that an in-flight personal write reaches the vault reseal
            // boundary before native state is released.
            requestExecutor.execute { personalApi.close() }
            requestExecutor.shutdown()
        }
    }

    private fun processRequest(request: BridgeRequest): String {
        val result = personalApi.requestWithAccess(request.method, request.path, request.body)
        if (result.access == "personal_write" && successful(result.envelope)) {
            onPersonalWriteCommitted?.invoke(request, result.envelope)
        }
        return result.envelope
    }

    private fun successful(rawEnvelope: String): Boolean = try {
        JSONObject(rawEnvelope).optInt("status") in 200..299
    } catch (_: Throwable) {
        false
    }
}
