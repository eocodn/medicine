package com.medicine.android

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
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
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.io.File
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private var webView: WebView? = null
    private var medicineBridge: MedicineBridge? = null
    private var personalDataRevisionGate: PersonalDataRevisionGate? = null
    private var appPageReady = false
    private lateinit var ocrIntegration: ProductCapabilityIntegration
    private val backDispatchGate = BackDispatchGate()
    private val medicineNativeProxy = MedicineNativeProxy()
    private var referenceBootstrapBridge: ReferenceBootstrapJsBridge? = null
    private var reminderNativeBridge: ReminderNativeBridge? = null
    private var webViewProviderPackageName: String? = null
    private var webViewProviderVersion: String? = null
    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        reminderNativeBridge?.onNotificationPermissionResult(granted)
    }

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
            PersonalDatabaseKeyStore::getOrCreate,
        )
        val bridge = MedicineBridge(
            reference.database,
            personalDatabase,
            vault,
            onPersonalWriteCommitted = { request, _ ->
                // Dashboard GET materializes planning rows and is classified as a personal
                // write by the core, but it is itself the UI's authoritative refresh. Only
                // non-GET commits need a later external-change invalidation signal.
                if (!request.method.equals("GET", ignoreCase = true)) {
                    PersonalDataRevision.markChanged(applicationContext)
                }
                ReminderMutationObserver.onCommitted(applicationContext, request)
            },
        )
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
        personalDataRevisionGate = PersonalDataRevisionGate(
            PersonalDataRevision.current(applicationContext),
        )
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
        val reminderBridge = ReminderNativeBridge(
            this,
            requestNotificationPermission = ::requestMedicationNotificationPermission,
            onStatusChanged = ::notifyReminderStatusChanged,
        )
        reminderNativeBridge = reminderBridge
        // onResume can run before WebView compatibility/bootstrap creates this bridge.
        // Refresh here as the cold-start boundary so alarms removed by force-stop are rebuilt
        // as soon as the user launches the app again.
        reminderBridge.refresh()

        view.addJavascriptInterface(medicineNativeProxy, "MedicineNative")
        view.addJavascriptInterface(bootstrapBridge, "MedicineBootstrapNative")
        view.addJavascriptInterface(reminderBridge, "MedicineReminderNative")
        ocrIntegration.configureWebView(view)
        appPageReady = false
        view.webViewClient = createAssetWebViewClient(assetLoader) { pageView, url ->
            if (url != APP_URL || pageView !== webView) return@createAssetWebViewClient
            appPageReady = true
            refreshPersonalDataIfChanged()
        }
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

    private fun requestMedicationNotificationPermission() {
        runOnUiThread {
            val runtimePermissionMissing = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
                    android.content.pm.PackageManager.PERMISSION_GRANTED
            if (runtimePermissionMissing) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                return@runOnUiThread
            }
            if (!ReminderPermissions.notificationsAllowed(this)) {
                try {
                    startActivity(
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                    )
                } catch (error: ActivityNotFoundException) {
                    Log.e(TAG, "Notification settings are unavailable", error)
                    reminderNativeBridge?.onNotificationPermissionResult(false)
                }
                return@runOnUiThread
            }
            reminderNativeBridge?.onNotificationPermissionResult(true)
        }
    }

    private fun notifyReminderStatusChanged() {
        runOnUiThread {
            if (isFinishing || isDestroyed) return@runOnUiThread
            webView?.evaluateJavascript("window.MedicineReminderUi?.refresh?.()", null)
        }
    }

    private fun refreshPersonalDataIfChanged() {
        if (!appPageReady || isFinishing || isDestroyed) return
        val gate = personalDataRevisionGate ?: return
        val revision = PersonalDataRevision.current(applicationContext)
        if (!gate.consumeIfChanged(revision)) return
        webView?.evaluateJavascript("window.MedicineApp?.refreshPersonalData?.()", null)
    }

    override fun onResume() {
        super.onResume()
        reminderNativeBridge?.refresh()
        refreshPersonalDataIfChanged()
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
        view.removeJavascriptInterface("MedicineReminderNative")
        view.clearHistory()
        view.removeAllViews()
        view.destroy()
        if (webView === view) {
            webView = null
            appPageReady = false
        }
    }

    private fun isAllowedOrigin(uri: Uri): Boolean =
        uri.scheme == "https" && uri.host == APP_ASSET_DOMAIN

    override fun onDestroy() {
        if (::ocrIntegration.isInitialized) ocrIntegration.close()
        referenceBootstrapBridge?.close()
        referenceBootstrapBridge = null
        reminderNativeBridge = null
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
    }
}
