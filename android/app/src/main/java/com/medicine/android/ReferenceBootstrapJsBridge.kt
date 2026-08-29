package com.medicine.android

import android.app.Activity
import android.webkit.JavascriptInterface
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class ReferenceBootstrapJsBridge(
    private val activity: Activity,
    private val onReferenceReady: (InstalledReference) -> Unit,
) : AutoCloseable {
    private val lock = Any()
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private var responseHandler: ((String, String) -> Unit)? = null
    private var installer: AndroidReferenceInstaller? = null
    private var preparation: ReferenceBootstrapPreparation? = null
    private var initialized = false
    private var operationRunning = false
    private var state = "checking"
    private var completedBytes = 0L
    private var totalBytes = 0L
    private var detail: String? = null

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
        val shouldStart = synchronized(lock) {
            if (initialized || operationRunning) false
            else {
                operationRunning = true
                true
            }
        }
        if (!shouldStart) return
        executor.execute {
            val result = runCatching {
                val installer = installer ?: AndroidReferenceInstaller(activity, observer()).also {
                    this.installer = it
                }
                installer to installer.prepare()
            }
            result.onSuccess { (installer, prepared) ->
                preparation = prepared
                if (prepared is ReferenceBootstrapPreparation.Download) {
                    synchronized(lock) {
                        initialized = true
                        operationRunning = false
                        state = "download_required"
                        totalBytes = prepared.totalDownloadBytes
                        completedBytes = totalBytes - prepared.downloadSizeBytes
                        detail = null
                    }
                } else {
                    completeInstall(installer, prepared)
                }
            }.onFailure(::fail)
        }
    }

    private fun startInstall() {
        var retryPrepare = false
        val work = synchronized(lock) {
            if (operationRunning || state !in setOf("download_required", "failed")) {
                null
            } else if (preparation == null) {
                initialized = false
                state = "checking"
                detail = null
                retryPrepare = true
                null
            } else {
                val currentInstaller = installer ?: return@synchronized null
                operationRunning = true
                state = "downloading"
                detail = null
                currentInstaller to preparation
            }
        }
        if (retryPrepare) {
            ensurePrepared()
            return
        }
        if (work == null) return
        executor.execute { completeInstall(work.first, work.second) }
    }

    private fun completeInstall(
        installer: AndroidReferenceInstaller,
        prepared: ReferenceBootstrapPreparation?,
    ) {
        runCatching {
            val reference = installer.installPrepared(prepared)
            onReferenceReady(reference)
            reference
        }.onSuccess { reference ->
            synchronized(lock) {
                initialized = true
                operationRunning = false
                state = if (reference.referenceAvailable) "ready" else "unavailable"
                if (state == "ready" && totalBytes > 0) completedBytes = totalBytes
                detail = reference.referenceUnavailableReason
            }
        }.onFailure(::fail)
    }

    private fun observer(): ReferenceUpdateObserver = object : ReferenceUpdateObserver {
        override fun phase(name: String) {
            synchronized(lock) {
                when (name) {
                    "manifest" -> state = "checking"
                    "full-download" -> state = "downloading"
                    "rebuild", "rebuild-checkpoint", "verify-and-install" -> state = "installing"
                }
            }
        }

        override fun progress(name: String, completedBytes: Long, totalBytes: Long) {
            if (name != "download" || totalBytes <= 0) return
            synchronized(lock) {
                state = "downloading"
                this@ReferenceBootstrapJsBridge.completedBytes = completedBytes
                this@ReferenceBootstrapJsBridge.totalBytes = totalBytes
            }
        }
    }

    private fun fail(error: Throwable) {
        synchronized(lock) {
            initialized = true
            operationRunning = false
            state = "failed"
            detail = when {
                error is ReferenceBootstrapStorageException -> "insufficient_storage"
                else -> "bootstrap_failed"
            }
        }
    }

    private fun snapshot(): String = synchronized(lock) {
        JSONObject()
            .put("state", state)
            .put("completed_bytes", completedBytes)
            .put("total_bytes", totalBytes)
            .put("detail", detail)
            .toString()
    }

    override fun close() {
        responseHandler = null
        executor.shutdownNow()
    }
}