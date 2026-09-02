package com.medicine.android

import android.app.Activity
import android.util.Log
import android.webkit.JavascriptInterface
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class ReferenceBootstrapJsBridge(
    private val activity: Activity,
    private val onReferenceReady: (InstalledReference) -> Unit,
) : AutoCloseable {
    private val lock = Any()
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private val coordinatorHandle = ReferenceNativeCore.createBootstrapCoordinator()
    private var responseHandler: ((String, String) -> Unit)? = null
    private var installer: AndroidReferenceInstaller? = null
    private var preparation: ReferenceBootstrapPreparation? = null

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
        if (!ReferenceNativeCore.bootstrapBeginPrepare(coordinatorHandle)) return
        executor.execute {
            val result = runCatching {
                val currentInstaller = synchronized(lock) {
                    installer ?: AndroidReferenceInstaller(activity, observer()).also {
                        installer = it
                    }
                }
                currentInstaller to currentInstaller.prepare()
            }
            result.onSuccess { (currentInstaller, prepared) ->
                synchronized(lock) { preparation = prepared }
                if (prepared is ReferenceBootstrapPreparation.Download) {
                    ReferenceNativeCore.bootstrapPreparedDownload(
                        coordinatorHandle,
                        prepared.totalDownloadBytes - prepared.downloadSizeBytes,
                        prepared.totalDownloadBytes,
                    )
                } else {
                    completeInstall(currentInstaller, prepared)
                }
            }.onFailure(::fail)
        }
    }

    private fun startInstall() {
        val work = synchronized(lock) {
            val prepared = preparation
            if (prepared == null) null else installer?.let { it to prepared }
        }
        if (work == null) {
            ReferenceNativeCore.bootstrapResetForPrepare(coordinatorHandle)
            ensurePrepared()
            return
        }
        if (!ReferenceNativeCore.bootstrapBeginInstall(coordinatorHandle)) return
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
            if (reference.referenceAvailable) {
                ReferenceNativeCore.bootstrapReady(coordinatorHandle)
            } else {
                ReferenceNativeCore.bootstrapUnavailable(
                    coordinatorHandle,
                    reference.referenceUnavailableReason ?: "update_required",
                )
            }
        }.onFailure(::fail)
    }

    private fun observer(): ReferenceUpdateObserver = object : ReferenceUpdateObserver {
        override fun phase(name: String) {
            ReferenceNativeCore.bootstrapPhase(coordinatorHandle, name)
        }

        override fun progress(name: String, completedBytes: Long, totalBytes: Long) {
            if (name != "download" || totalBytes <= 0) return
            ReferenceNativeCore.bootstrapProgress(coordinatorHandle, completedBytes, totalBytes)
        }
    }

    private fun fail(error: Throwable) {
        Log.e(TAG, "Reference bootstrap failed", error)
        val detail = when {
            error is ReferenceBootstrapStorageException -> "insufficient_storage"
            error.hasCause<UnknownHostException>() ||
                error.hasCause<ConnectException>() ||
                error.hasCause<NoRouteToHostException>() ||
                error.hasCause<SocketTimeoutException>() ||
                error.hasCause<SSLException>() -> "network_failed"
            error is ReferenceManifestStageException -> error.stage
            error is ReferenceBootstrapPrepareStageException -> error.stage
            BuildConfig.DEBUG -> debugFailureDetail(error)
            else -> "phase_failed"
        }
        ReferenceNativeCore.bootstrapFailed(
            coordinatorHandle,
            detail,
        )
    }

    private fun debugFailureDetail(error: Throwable): String {
        val type = error.javaClass.simpleName
            .replace(Regex("[^A-Za-z0-9]+"), "_")
            .trim('_')
            .take(80)
            .ifEmpty { "Throwable" }
        val message = error.message
            ?.replace(Regex("[\r\n\t]+"), " ")
            ?.replace(Regex("[^A-Za-z0-9 ._:/-]+"), "_")
            ?.trim()
            ?.take(160)
            .orEmpty()
        return if (message.isEmpty()) "debug_$type" else "debug_${type}_$message"
    }

    private inline fun <reified T : Throwable> Throwable.hasCause(): Boolean {
        var current: Throwable? = this
        while (current != null) {
            if (current is T) return true
            current = current.cause
        }
        return false
    }

    private fun snapshot(): String = ReferenceNativeCore.bootstrapSnapshot(coordinatorHandle)

    override fun close() {
        responseHandler = null
        executor.shutdownNow()
        ReferenceNativeCore.destroyBootstrapCoordinator(coordinatorHandle)
    }

    companion object {
        private const val TAG = "ReferenceBootstrap"
    }
}