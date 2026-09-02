package com.medicine.android

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.io.File
import java.security.KeyStore
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private var webView: WebView? = null
    private var medicineBridge: MedicineBridge? = null
    private lateinit var ocrIntegration: ProductCapabilityIntegration
    private val backDispatchGate = BackDispatchGate()
    private val startupExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val medicineNativeProxy = MedicineNativeProxy()
    private var referenceBootstrapBridge: ReferenceBootstrapJsBridge? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ocrIntegration = ProductCapabilityIntegration(this)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                handleAppBack(this)
            }
        })
        setupWebView()
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

    private fun activateReference(reference: InstalledReference) {
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
        medicineBridge?.let {
            medicineNativeProxy.detach(it)
            it.close()
        }
        medicineBridge = bridge
        medicineNativeProxy.attach(bridge)
        scheduleReferenceUpdate(reference, bridge)
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

    private fun setupWebView() {
        val assetLoaderBuilder = WebViewAssetLoader.Builder()
            .setDomain(APP_ASSET_DOMAIN)
            .addPathHandler("/static/", WebViewAssetLoader.AssetsPathHandler(this))
        ocrIntegration.configureAssetLoader(assetLoaderBuilder)
        val assetLoader = assetLoaderBuilder.build()
        val bootstrapBridge = ReferenceBootstrapJsBridge(this, ::activateReference)
        referenceBootstrapBridge = bootstrapBridge

        val view = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.cacheMode = WebSettings.LOAD_NO_CACHE

            addJavascriptInterface(medicineNativeProxy, "MedicineNative")
            addJavascriptInterface(bootstrapBridge, "MedicineBootstrapNative")
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
        bootstrapBridge.setResponseHandler { requestId, response ->
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                val target = webView ?: return@runOnUiThread
                target.evaluateJavascript(
                    "window.MedicineBootstrapUi?.resolve(${JSONObject.quote(requestId)}, ${JSONObject.quote(response)})",
                    null,
                )
            }
        }
        setContentView(view)
        view.loadUrl(APP_URL)
    }

    private fun isAllowedOrigin(uri: Uri): Boolean =
        uri.scheme == "https" && uri.host == APP_ASSET_DOMAIN

    override fun onDestroy() {
        ocrIntegration.close()
        startupExecutor.shutdownNow()
        referenceBootstrapBridge?.close()
        referenceBootstrapBridge = null
        medicineBridge?.close()
        medicineBridge = null
        webView?.let { view ->
            view.stopLoading()
            view.removeJavascriptInterface("MedicineNative")
            view.removeJavascriptInterface("MedicineBootstrapNative")
            view.clearHistory()
            view.removeAllViews()
            view.destroy()
        }
        webView = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "MainActivity"
        private const val APP_ASSET_DOMAIN = "appassets.androidplatform.net"
        private const val APP_URL = "https://$APP_ASSET_DOMAIN/static/index.html"
        private const val PERSONAL_DB_KEY_ALIAS = "medicine.personal-db.v1"
    }
}
