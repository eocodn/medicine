package com.medicine.android

import android.content.ActivityNotFoundException
import android.content.Intent
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
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
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.net.toUri
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.io.File
import java.security.KeyStore
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private var webView: WebView? = null
    private var medicineBridge: MedicineBridge? = null
    private lateinit var ocrIntegration: ProductCapabilityIntegration
    private val backDispatchGate = BackDispatchGate()
    private val medicineNativeProxy = MedicineNativeProxy()
    private var referenceBootstrapBridge: ReferenceBootstrapJsBridge? = null
    private var webViewProviderPackageName: String? = null
    private var webViewProviderVersion: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val webViewPackage = WebView.getCurrentWebViewPackage()
        webViewProviderPackageName = webViewPackage?.packageName
        webViewProviderVersion = webViewPackage?.versionName
        if (!WebViewCompatibility.isSupportedVersion(webViewProviderVersion)) {
            Log.w(
                TAG,
                "Unsupported WebView provider: ${webViewProviderPackageName.orEmpty()} ${webViewProviderVersion.orEmpty()}",
            )
            showUnsupportedWebView()
            return
        }
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

        val view = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.cacheMode = WebSettings.LOAD_NO_CACHE
        }
        webView = view
        view.webViewClient = createAssetWebViewClient(assetLoader) { pageView, url ->
            if (url != COMPATIBILITY_CHECK_URL || pageView !== webView) return@createAssetWebViewClient
            pageView.evaluateJavascript(WebViewCompatibility.CAPABILITY_PROBE) { result ->
                if (isFinishing || isDestroyed || pageView !== webView) return@evaluateJavascript
                if (!WebViewCompatibility.capabilitiesSatisfied(result)) {
                    Log.w(
                        TAG,
                        "WebView capability probe failed for ${webViewProviderPackageName.orEmpty()} ${webViewProviderVersion.orEmpty()}",
                    )
                    destroyWebView(pageView)
                    showUnsupportedWebView()
                    return@evaluateJavascript
                }
                configureAppWebView(pageView, assetLoader)
            }
        }
        setContentView(view)
        view.loadUrl(COMPATIBILITY_CHECK_URL)
    }

    private fun configureAppWebView(view: WebView, assetLoader: WebViewAssetLoader) {
        val bootstrapBridge = ReferenceBootstrapJsBridge(
            this,
            ::activateReference,
            onReferenceRetired = { reason -> medicineBridge?.setReferenceAvailable(false, reason) },
        )
        referenceBootstrapBridge = bootstrapBridge

        view.addJavascriptInterface(medicineNativeProxy, "MedicineNative")
        view.addJavascriptInterface(bootstrapBridge, "MedicineBootstrapNative")
        ocrIntegration.configureWebView(view)
        view.webViewClient = createAssetWebViewClient(assetLoader)
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
        view.loadUrl(APP_URL)
    }

    private fun createAssetWebViewClient(
        assetLoader: WebViewAssetLoader,
        onPageFinished: ((WebView, String) -> Unit)? = null,
    ): WebViewClient = object : WebViewClient() {
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
            !isAllowedOrigin(url.toUri())

        override fun onPageFinished(view: WebView, url: String) {
            super.onPageFinished(view, url)
            onPageFinished?.invoke(view, url)
        }
    }

    private fun showUnsupportedWebView() {
        val providerName = webViewProviderPackageName?.let { packageName ->
            runCatching {
                packageManager.getApplicationLabel(
                    packageManager.getApplicationInfo(packageName, 0),
                ).toString()
            }.getOrDefault(packageName)
        } ?: getString(R.string.webview_provider_unknown)
        val providerVersion = webViewProviderVersion
            ?.takeIf { it.isNotBlank() }
            ?: getString(R.string.webview_provider_unknown)
        val padding = (24 * resources.displayMetrics.density).toInt()

        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(padding, padding, padding, padding)
        }
        val title = TextView(this).apply {
            text = getString(R.string.webview_update_title)
            textSize = 24f
        }
        val message = TextView(this).apply {
            text = getString(R.string.webview_update_message, providerName, providerVersion)
            textSize = 16f
            setPadding(0, padding, 0, padding)
        }
        val updateButton = Button(this).apply {
            text = getString(R.string.webview_update_button)
            setOnClickListener { openWebViewUpdate() }
        }
        content.addView(
            title,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        content.addView(
            message,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        content.addView(
            updateButton,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        setContentView(content)
    }

    private fun openWebViewUpdate() {
        val packageName = webViewProviderPackageName ?: DEFAULT_WEBVIEW_PACKAGE
        val marketIntent = Intent(
            Intent.ACTION_VIEW,
            "market://details?id=$packageName".toUri(),
        )
        try {
            startActivity(marketIntent)
        } catch (_: ActivityNotFoundException) {
            val browserIntent = Intent(
                Intent.ACTION_VIEW,
                "https://play.google.com/store/apps/details?id=$packageName".toUri(),
            )
            try {
                startActivity(browserIntent)
            } catch (_: ActivityNotFoundException) {
                Toast.makeText(
                    this,
                    R.string.webview_update_store_unavailable,
                    Toast.LENGTH_LONG,
                ).show()
            }
        }
    }

    private fun destroyWebView(view: WebView) {
        view.stopLoading()
        view.removeJavascriptInterface("MedicineNative")
        view.removeJavascriptInterface("MedicineBootstrapNative")
        view.clearHistory()
        view.removeAllViews()
        view.destroy()
        if (webView === view) webView = null
    }

    private fun isAllowedOrigin(uri: Uri): Boolean =
        uri.scheme == "https" && uri.host == APP_ASSET_DOMAIN

    override fun onDestroy() {
        if (::ocrIntegration.isInitialized) ocrIntegration.close()
        referenceBootstrapBridge?.close()
        referenceBootstrapBridge = null
        medicineBridge?.close()
        medicineBridge = null
        webView?.let(::destroyWebView)
        super.onDestroy()
    }

    companion object {
        private const val TAG = "MainActivity"
        private const val APP_ASSET_DOMAIN = "appassets.androidplatform.net"
        private const val COMPATIBILITY_CHECK_URL =
            "https://$APP_ASSET_DOMAIN/static/webview-compatibility.html"
        private const val APP_URL = "https://$APP_ASSET_DOMAIN/static/index.html"
        private const val DEFAULT_WEBVIEW_PACKAGE = "com.google.android.webview"
        private const val PERSONAL_DB_KEY_ALIAS = "medicine.personal-db.v1"
    }
}
