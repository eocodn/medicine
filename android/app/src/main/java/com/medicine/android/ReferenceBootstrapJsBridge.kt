package com.medicine.android

import android.app.Activity
import android.util.Log
import android.webkit.JavascriptInterface
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class ReferenceBootstrapJsBridge(
    private val activity: Activity,
    private val onReferenceReady: (InstalledReference) -> Unit,
    private val onReferenceRetired: (String) -> Unit,
) : AutoCloseable {
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private val prepareScheduled = AtomicBoolean(false)
    private val updateScheduled = AtomicBoolean(false)
    private val runtimeHandle: Long = ReferenceNativeCore.createReferenceRuntime(
        File(activity.filesDir, "reference"),
        BuildConfig.REFERENCE_UPDATE_BASE_URL.trim().also {
            require(it.isNotEmpty()) { "reference distribution base URL is not configured" }
        },
    )
    @Volatile private var responseHandler: ((String, String) -> Unit)? = null

    fun setResponseHandler(handler: (String, String) -> Unit) {
        responseHandler = handler
    }

    @JavascriptInterface
    fun requestAsync(requestId: String, action: String) {
        when (action) {
            "status" -> ensurePrepared()
            "start" -> startInstall()
        }
        responseHandler?.invoke(requestId, snapshot())
    }

    @JavascriptInterface
    fun closeApp() {
        activity.runOnUiThread { activity.finishAndRemoveTask() }
    }

    private fun ensurePrepared() {
        if (!prepareScheduled.compareAndSet(false, true)) return
        executor.execute {
            try {
                handleOperation(ReferenceNativeCore.prepare(runtimeHandle))
            } catch (error: Throwable) {
                Log.e(TAG, "Reference bootstrap prepare failed", error)
            } finally {
                prepareScheduled.set(false)
            }
        }
    }

    private fun startInstall() {
        executor.execute {
            runCatching { ReferenceNativeCore.start(runtimeHandle) }
                .onSuccess(::handleOperation)
                .onFailure { error -> Log.e(TAG, "Reference bootstrap install failed", error) }
        }
    }

    private fun handleOperation(operation: ReferenceRuntimeOperation) {
        operation.error?.let { detail ->
            Log.e(TAG, "Reference runtime failed: $detail")
        }
        val reference = operation.selection ?: return
        onReferenceReady(reference)
        if (reference.referenceAvailable) scheduleReferenceUpdate()
    }

    private fun scheduleReferenceUpdate() {
        if (!updateScheduled.compareAndSet(false, true)) return
        executor.execute {
            val result = runCatching { ReferenceNativeCore.checkForUpdate(runtimeHandle) }
                .getOrElse { error ->
                    Log.e(TAG, "Reference update failed", error)
                    return@execute
                }
            when (result.status) {
                ReferenceUpdateStatus.FAILED ->
                    Log.e(TAG, "Reference update failed: ${result.detail.orEmpty()}")
                ReferenceUpdateStatus.UPDATE_REQUIRED -> {
                    onReferenceRetired("update_required")
                    Log.w(TAG, "Reference contract retired; safety-data operations are blocked")
                }
                ReferenceUpdateStatus.STAGED ->
                    Log.i(TAG, "Reference update staged for next startup")
                ReferenceUpdateStatus.UP_TO_DATE ->
                    Log.i(TAG, "Reference update check completed: no change")
            }
        }
    }

    private fun snapshot(): String = runCatching {
        ReferenceNativeCore.status(runtimeHandle)
    }.getOrElse { error ->
        Log.e(TAG, "Reference runtime status failed", error)
        "{\"state\":\"failed\",\"completed_bytes\":0,\"total_bytes\":0,\"detail\":\"runtime_state_unavailable\"}"
    }

    override fun close() {
        responseHandler = null
        executor.shutdownNow()
        ReferenceNativeCore.destroyReferenceRuntime(runtimeHandle)
    }

    companion object {
        private const val TAG = "ReferenceRuntime"
    }
}
