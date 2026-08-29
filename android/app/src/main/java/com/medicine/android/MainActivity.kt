package com.medicine.android

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.app.AlertDialog
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.io.File
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.security.KeyStore
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.atomic.AtomicBoolean
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private var webView: WebView? = null
    private var medicineBridge: MedicineBridge? = null
    private lateinit var ocrIntegration: ProductCapabilityIntegration
    private val backDispatchGate = BackDispatchGate()
    private val startupExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val startupRunning = AtomicBoolean(false)
    private var bootstrapDialog: AlertDialog? = null
    private var bootstrapProgressBar: ProgressBar? = null
    private var bootstrapStatusText: TextView? = null
    private var bootstrapProgressText: TextView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ocrIntegration = ProductCapabilityIntegration(this)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                handleAppBack(this)
            }
        })
        startApplication()
    }

    private fun handleAppBack(callback: OnBackPressedCallback) {
        val view = webView
        if (view == null) {
            dispatchDefaultBack(callback)
            return
        }
        if (!backDispatchGate.tryBegin()) return
        view.evaluateJavascript("window.MedicineDialog?.handleNativeBack?.() === true") { handled ->
            val dispatchDefault = backDispatchGate.complete(handled, isFinishing || isDestroyed)
            if (dispatchDefault) dispatchDefaultBack(callback)
        }
    }

    private fun dispatchDefaultBack(callback: OnBackPressedCallback) {
        callback.isEnabled = false
        onBackPressedDispatcher.onBackPressed()
        callback.isEnabled = true
    }

    private fun startApplication() {
        if (!startupRunning.compareAndSet(false, true)) return
        showStartupView("안전 데이터 확인 중…")
        startupExecutor.execute {
            runCatching {
                val installer = AndroidReferenceInstaller(
                    this,
                    object : ReferenceUpdateObserver {
                        override fun phase(name: String) {
                            val message = when (name) {
                                "manifest" -> "안전 데이터 확인 중…"
                                "full-download" -> "안전 데이터 다운로드 중…"
                                "rebuild" -> "안전 데이터 설치 중…"
                                "rebuild-checkpoint" -> "안전 데이터 설치 중…"
                                "verify-and-install" -> "안전 데이터 검증 중…"
                                else -> return
                            }
                            runOnUiThread {
                                if (!isFinishing && !isDestroyed) {
                                    if (bootstrapDialog != null) updateBootstrapDialog(message)
                                    else showStartupView(message)
                                }
                            }
                        }

                        override fun progress(name: String, completedBytes: Long, totalBytes: Long) {
                            if (totalBytes <= 0) return
                            val message = when {
                                name == "download" -> "안전 데이터 다운로드 중…"
                                name.startsWith("rebuild-") -> "안전 데이터 설치 중…"
                                else -> return
                            }
                            val percent = ((completedBytes * 100L) / totalBytes).coerceIn(0L, 100L).toInt()
                            runOnUiThread {
                                if (!isFinishing && !isDestroyed) {
                                    if (bootstrapDialog != null) {
                                        updateBootstrapDialog(
                                            message,
                                            completedBytes,
                                            totalBytes,
                                            percent,
                                        )
                                    } else {
                                        showStartupView(message, progressPercent = percent)
                                    }
                                }
                            }
                        }
                    },
                )
                installer to installer.prepare()
            }.onSuccess { (installer, preparation) ->
                if (preparation is ReferenceBootstrapPreparation.Download) {
                    runOnUiThread {
                        if (!isFinishing && !isDestroyed) {
                            showBootstrapDownloadPrompt(preparation) {
                                continueApplicationStartup(installer, preparation)
                            }
                        }
                    }
                } else {
                    continueApplicationStartup(installer, preparation)
                }
            }.onFailure { error ->
                handleStartupFailure(error)
            }
        }
    }

    private fun continueApplicationStartup(
        installer: AndroidReferenceInstaller,
        preparation: ReferenceBootstrapPreparation?,
    ) {
        startupExecutor.execute {
            runCatching {
                val reference = installer.installPrepared(preparation)
                reference.recoveryReason?.let { reason ->
                    Log.w(TAG, "Reference store recovery: $reason")
                }
                val personalDatabase = File(filesDir, "personal.sqlite")
                val encryptedPersonalDatabase = File(filesDir, "personal.sqlite.enc")
                val vault = PersonalDatabaseVault(
                    personalDatabase,
                    encryptedPersonalDatabase,
                    ::personalDatabaseKey,
                )
                val bridge = MedicineBridge(reference.database, personalDatabase, vault)
                if (!reference.referenceAvailable) {
                    bridge.setReferenceAvailable(
                        false,
                        reference.referenceUnavailableReason ?: "update_required",
                    )
                }
                StartupSession(bridge = bridge, reference = reference)
            }.onSuccess { session ->
                startupRunning.set(false)
                runOnUiThread {
                    if (!isFinishing && !isDestroyed) {
                        dismissBootstrapDialog()
                        setupWebView(session.bridge)
                    }
                }
                scheduleReferenceUpdate(session.reference, session.bridge)
            }.onFailure(::handleStartupFailure)
        }
    }

    private fun handleStartupFailure(error: Throwable) {
        startupRunning.set(false)
        Log.e(TAG, "Application startup failed", error)
        runOnUiThread {
            if (!isFinishing && !isDestroyed) {
                dismissBootstrapDialog()
                showStartupView(startupFailureMessage(error), retry = true)
            }
        }
    }

    private fun startupFailureMessage(error: Throwable): String = when {
        error is ReferenceContractRetiredException ->
            "현재 앱 버전의 안전 데이터 지원이 종료되었습니다.\n앱을 업데이트해주세요."
        error is ReferenceBootstrapStorageException ->
            "안전 데이터를 저장할 공간이 부족합니다.\n공간을 확보한 뒤 다시 시도해주세요."
        error.hasCause<UnknownHostException>() ||
            error.hasCause<ConnectException>() ||
            error.hasCause<SocketTimeoutException>() ->
            "안전 데이터를 받으려면 인터넷 연결이 필요합니다.\n연결 후 다시 시도해주세요."
        error is IllegalArgumentException || error is IllegalStateException ->
            "안전 데이터 검증에 실패했습니다.\n다시 시도해주세요."
        else -> "앱 데이터를 준비하지 못했습니다.\n다시 시도해주세요."
    }

    private inline fun <reified T : Throwable> Throwable.hasCause(): Boolean {
        var current: Throwable? = this
        while (current != null) {
            if (current is T) return true
            current = current.cause
        }
        return false
    }

    private fun scheduleReferenceUpdate(reference: InstalledReference, bridge: MedicineBridge) {
        val installedVersion = reference.version ?: return
        val installedDatabase = reference.database ?: return
        val baseUrl = BuildConfig.REFERENCE_UPDATE_BASE_URL.trim()
        if (baseUrl.isEmpty()) {
            Log.i(TAG, "Reference updater is disabled: no distribution base URL configured")
            return
        }
        if (isDestroyed || startupExecutor.isShutdown) return
        try {
            startupExecutor.execute {
                val result = runCatching {
                    val source = HttpsReferenceReleaseSource(
                        baseUrl,
                        ReferenceManifestVerifier(ReferenceTrust.trustedPublicKeys),
                    )
                    ReferenceUpdater(
                        reference.referenceDir,
                        reference.store,
                        source,
                        RustReferenceArtifactRebuilder(),
                        ReferenceUpdateLogObserver(),
                    ).checkForUpdate(InstalledReferenceVersion(installedVersion, installedDatabase))
                }.getOrElse { error ->
                    ReferenceUpdateResult(
                        status = ReferenceUpdateStatus.FAILED,
                        detail = error.message ?: error.javaClass.simpleName,
                    )
                }
                if (result.status == ReferenceUpdateStatus.FAILED) {
                    Log.e(TAG, "Reference update failed: ${result.detail}")
                } else if (result.status == ReferenceUpdateStatus.UPDATE_REQUIRED) {
                    bridge.setReferenceAvailable(false, "update_required")
                    Log.w(TAG, "Reference contract retired; safety-data operations are blocked")
                } else {
                    Log.i(TAG, "Reference update result=${result.status} sequence=${result.releaseSequence}")
                }
            }
        } catch (_: RejectedExecutionException) {
            // Activity destruction can race a completed startup worker. Reference
            // state is already safe; simply avoid queueing lifecycle-owned OTA work.
            Log.i(TAG, "Reference updater skipped because Activity executor is closed")
        }
    }

    private fun personalDatabaseKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(PERSONAL_DB_KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                PERSONAL_DB_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    private fun setupWebView(bridge: MedicineBridge) {
        val assetLoaderBuilder = WebViewAssetLoader.Builder()
            .setDomain(APP_ASSET_DOMAIN)
            .addPathHandler("/static/", WebViewAssetLoader.AssetsPathHandler(this))
        ocrIntegration.configureAssetLoader(assetLoaderBuilder)
        val assetLoader = assetLoaderBuilder.build()

        val view = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW

            addJavascriptInterface(bridge, "MedicineNative")
            ocrIntegration.configureWebView(this)
            webViewClient = object : WebViewClient() {
                override fun shouldInterceptRequest(
                    view: WebView,
                    request: WebResourceRequest,
                ): WebResourceResponse? {
                    val url = request.url
                    if (url.scheme == "https" && url.host == APP_ASSET_DOMAIN) {
                        return assetLoader.shouldInterceptRequest(url)
                    }
                    if (url.scheme == "http" || url.scheme == "https") {
                        return WebResourceResponse(
                            "text/plain",
                            "utf-8",
                            403,
                            "Blocked",
                            emptyMap(),
                            ByteArrayInputStream("blocked external request".toByteArray()),
                        )
                    }
                    return super.shouldInterceptRequest(view, request)
                }

                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest,
                ): Boolean = !isAllowedOrigin(request.url)

                @Suppress("DEPRECATION")
                override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean =
                    !isAllowedOrigin(Uri.parse(url))
            }
        }
        webView = view
        medicineBridge = bridge
        bridge.setResponseHandler { requestId, response ->
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                val target = webView ?: return@runOnUiThread
                target.evaluateJavascript(
                    "window.MedicineLocalApi?.resolve(${JSONObject.quote(requestId)}, ${JSONObject.quote(response)})",
                    null,
                )
            }
        }
        setContentView(view)
        view.loadUrl(APP_URL)
    }

    private fun showBootstrapDownloadPrompt(
        preparation: ReferenceBootstrapPreparation.Download,
        onDownload: () -> Unit,
    ) {
        dismissBootstrapDialog()
        val totalBytes = preparation.totalDownloadBytes
        val completedBytes = totalBytes - preparation.downloadSizeBytes
        val status = TextView(this).apply {
            text = "처음 사용하려면 약 안전 데이터 DB ${formatByteCount(totalBytes)}를 다운로드해야 합니다.\n다운로드하지 않으면 앱을 사용할 수 없습니다."
            textSize = 16f
            setPadding(0, 0, 0, 18)
        }
        val progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            isIndeterminate = false
            this.progress = if (totalBytes > 0) {
                ((completedBytes * 100L) / totalBytes).coerceIn(0L, 100L).toInt()
            } else {
                0
            }
            visibility = android.view.View.GONE
        }
        val progressText = TextView(this).apply {
            text = "${formatByteCount(completedBytes)} / ${formatByteCount(totalBytes)}"
            textSize = 14f
            setPadding(0, 10, 0, 0)
            visibility = android.view.View.GONE
        }
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 8, 24, 0)
            addView(status)
            addView(progress, ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ))
            addView(progressText)
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle("안전 데이터 다운로드")
            .setView(content)
            .setNegativeButton("앱 종료") { _, _ -> finishAndRemoveTask() }
            .setPositiveButton("다운로드", null)
            .create()
        dialog.setCancelable(false)
        dialog.setCanceledOnTouchOutside(false)
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled = false
                dialog.getButton(AlertDialog.BUTTON_NEGATIVE).isEnabled = false
                progress.visibility = android.view.View.VISIBLE
                progressText.visibility = android.view.View.VISIBLE
                status.text = "안전 데이터 다운로드 중…"
                onDownload()
            }
        }
        bootstrapDialog = dialog
        bootstrapProgressBar = progress
        bootstrapStatusText = status
        bootstrapProgressText = progressText
        dialog.show()
    }

    private fun updateBootstrapDialog(
        message: String,
        completedBytes: Long? = null,
        totalBytes: Long? = null,
        progressPercent: Int? = null,
    ) {
        bootstrapStatusText?.text = message
        bootstrapProgressBar?.let { bar ->
            if (progressPercent != null) {
                bar.visibility = android.view.View.VISIBLE
                bar.isIndeterminate = false
                bar.progress = progressPercent
            } else if (message.contains("설치") || message.contains("검증")) {
                bar.visibility = android.view.View.VISIBLE
                bar.isIndeterminate = true
            }
        }
        if (completedBytes != null && totalBytes != null && totalBytes > 0) {
            bootstrapProgressText?.apply {
                visibility = android.view.View.VISIBLE
                text = "${formatByteCount(completedBytes)} / ${formatByteCount(totalBytes)} · ${progressPercent ?: 0}%"
            }
        }
    }

    private fun dismissBootstrapDialog() {
        bootstrapDialog?.dismiss()
        bootstrapDialog = null
        bootstrapProgressBar = null
        bootstrapStatusText = null
        bootstrapProgressText = null
    }

    private fun formatByteCount(bytes: Long): String {
        val mib = bytes.toDouble() / (1024.0 * 1024.0)
        return if (mib >= 10.0) "%.0f MB".format(mib) else "%.1f MB".format(mib)
    }

    private fun isAllowedOrigin(uri: Uri): Boolean =
        uri.scheme == "https" && uri.host == APP_ASSET_DOMAIN

    private fun showStartupView(
        message: String,
        progressPercent: Int? = null,
        retry: Boolean = false,
    ) {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(48, 48, 48, 48)
            addView(ProgressBar(this@MainActivity, null, android.R.attr.progressBarStyleHorizontal).apply {
                isIndeterminate = progressPercent == null
                max = 100
                if (progressPercent != null) progress = progressPercent
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                )
            })
            addView(TextView(this@MainActivity).apply {
                text = message
                gravity = Gravity.CENTER
                textSize = 16f
                setPadding(0, 24, 0, 0)
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                )
            })
            if (retry) {
                addView(Button(this@MainActivity).apply {
                    text = "다시 시도"
                    setPadding(32, 16, 32, 16)
                    setOnClickListener { startApplication() }
                })
            }
        }
        setContentView(root)
    }

    override fun onDestroy() {
        dismissBootstrapDialog()
        ocrIntegration.close()
        startupExecutor.shutdownNow()
        medicineBridge?.close()
        medicineBridge = null
        webView?.let { view ->
            view.stopLoading()
            view.removeJavascriptInterface("MedicineNative")
            view.clearHistory()
            view.removeAllViews()
            view.destroy()
        }
        webView = null
        super.onDestroy()
    }

    private data class StartupSession(
        val bridge: MedicineBridge,
        val reference: InstalledReference,
    )

    companion object {
        private const val TAG = "MainActivity"
        private const val APP_ASSET_DOMAIN = "appassets.androidplatform.net"
        private const val APP_URL = "https://$APP_ASSET_DOMAIN/static/index.html"
        private const val PERSONAL_DB_KEY_ALIAS = "medicine.personal-db.v1"
    }
}
