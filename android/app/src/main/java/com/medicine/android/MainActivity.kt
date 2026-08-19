package com.medicine.android

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.provider.MediaStore
import android.os.Bundle
import android.util.Log
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
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
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
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

class MainActivity : ComponentActivity() {
    private var webView: WebView? = null
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var pendingCaptureUri: Uri? = null
    private var pendingCaptureFile: File? = null
    private var backDispatchPending = false
    private val startupExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val startupRunning = AtomicBoolean(false)
    private val fileChooserLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val callback = fileChooserCallback
        fileChooserCallback = null
        val captureUri = pendingCaptureUri
        val captureFile = pendingCaptureFile
        pendingCaptureUri = null
        pendingCaptureFile = null
        val picked = if (result.resultCode == Activity.RESULT_OK) {
            WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        } else {
            null
        }
        val usedCamera = result.resultCode == Activity.RESULT_OK && picked.isNullOrEmpty() && captureUri != null
        if (!usedCamera) captureFile?.delete()
        callback?.onReceiveValue(if (usedCamera) arrayOf(captureUri!!) else picked)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                handleAppBack(this)
            }
        })
        startApplication()
    }

    private fun handleAppBack(callback: OnBackPressedCallback) {
        if (backDispatchPending) return
        val view = webView
        if (view == null) {
            dispatchDefaultBack(callback)
            return
        }
        backDispatchPending = true
        view.evaluateJavascript("window.MedicineDialog?.handleNativeBack?.() === true") { handled ->
            backDispatchPending = false
            if (handled == "true" || isFinishing || isDestroyed) return@evaluateJavascript
            dispatchDefaultBack(callback)
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
                val reference = AndroidReferenceInstaller(
                    this,
                    object : ReferenceUpdateObserver {
                        override fun phase(name: String) {
                            val message = when (name) {
                                "manifest" -> "안전 데이터 확인 중…"
                                "full-download" -> "안전 데이터 다운로드 중…"
                                "rebuild" -> "안전 데이터 설치 중…"
                                "verify-and-install" -> "안전 데이터 검증 중…"
                                else -> return
                            }
                            runOnUiThread {
                                if (!isFinishing && !isDestroyed) showStartupView(message)
                            }
                        }

                        override fun progress(name: String, completedBytes: Long, totalBytes: Long) {
                            if (name != "download" || totalBytes <= 0) return
                            val percent = ((completedBytes * 100L) / totalBytes).coerceIn(0L, 100L).toInt()
                            runOnUiThread {
                                if (!isFinishing && !isDestroyed) {
                                    showStartupView("안전 데이터 다운로드 중…", progressPercent = percent)
                                }
                            }
                        }
                    },
                ).install()
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
                StartupSession(
                    bridge = MedicineBridge(reference.database, personalDatabase, vault),
                    reference = reference,
                )
            }.onSuccess { session ->
                startupRunning.set(false)
                runOnUiThread {
                    if (!isFinishing && !isDestroyed) setupWebView(session.bridge)
                }
                scheduleReferenceUpdate(session.reference)
            }.onFailure { error ->
                startupRunning.set(false)
                Log.e(TAG, "Application startup failed", error)
                runOnUiThread {
                    if (!isFinishing && !isDestroyed) {
                        showStartupView(startupFailureMessage(error), retry = true)
                    }
                }
            }
        }
    }

    private fun startupFailureMessage(error: Throwable): String = when {
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

    private fun scheduleReferenceUpdate(reference: InstalledReference) {
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
                        PythonReferenceArtifactRebuilder(),
                        ReferenceUpdateLogObserver(),
                    ).checkForUpdate(InstalledReferenceVersion(reference.version, reference.database))
                }.getOrElse { error ->
                    ReferenceUpdateResult(
                        status = ReferenceUpdateStatus.FAILED,
                        detail = error.message ?: error.javaClass.simpleName,
                    )
                }
                if (result.status == ReferenceUpdateStatus.FAILED) {
                    Log.e(TAG, "Reference update failed: ${result.detail}")
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
        val assetLoader = WebViewAssetLoader.Builder()
            .setDomain(APP_ASSET_DOMAIN)
            .addPathHandler("/static/", WebViewAssetLoader.AssetsPathHandler(this))
            .addPathHandler("/ocr-assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        val view = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW

            addJavascriptInterface(bridge, "MedicineNative")
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(
                    webView: WebView?,
                    filePathCallback: ValueCallback<Array<Uri>>,
                    fileChooserParams: FileChooserParams,
                ): Boolean {
                    fileChooserCallback?.onReceiveValue(null)
                    pendingCaptureFile?.delete()
                    pendingCaptureFile = null
                    pendingCaptureUri = null
                    fileChooserCallback = filePathCallback
                    return try {
                        val cameraIntent = createCameraCaptureIntent()
                        val chooser = Intent.createChooser(fileChooserParams.createIntent(), "처방전 사진 선택").apply {
                            putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(cameraIntent))
                        }
                        fileChooserLauncher.launch(chooser)
                        true
                    } catch (error: Exception) {
                        pendingCaptureFile?.delete()
                        pendingCaptureFile = null
                        pendingCaptureUri = null
                        fileChooserCallback = null
                        filePathCallback.onReceiveValue(null)
                        false
                    }
                }
            }
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
            loadUrl(APP_URL)
        }
        webView = view
        setContentView(view)
    }

    private fun createCameraCaptureIntent(): Intent {
        val directory = File(cacheDir, "ocr-capture").apply { mkdirs() }
        directory.listFiles()?.forEach { file -> file.delete() }
        val captureFile = File.createTempFile("prescription-", ".jpg", directory)
        val captureUri = FileProvider.getUriForFile(this, "$packageName.fileprovider", captureFile)
        pendingCaptureFile = captureFile
        pendingCaptureUri = captureUri
        return Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
            putExtra(MediaStore.EXTRA_OUTPUT, captureUri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        }
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
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = null
        pendingCaptureFile?.delete()
        pendingCaptureFile = null
        pendingCaptureUri = null
        startupExecutor.shutdownNow()
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
