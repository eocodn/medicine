package com.medicine.android

import android.content.Intent
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.net.Uri
import android.os.Bundle
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
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.io.File
import java.security.KeyStore
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

class MainActivity : ComponentActivity() {
    private var webView: WebView? = null
    private lateinit var fileChooserLauncher: ActivityResultLauncher<Intent>
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private val startupExecutor: ExecutorService = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        fileChooserLauncher = registerForActivityResult(
            ActivityResultContracts.StartActivityForResult()
        ) { result ->
            val selected = WebChromeClient.FileChooserParams.parseResult(
                result.resultCode,
                result.data
            )
            fileChooserCallback?.onReceiveValue(selected)
            fileChooserCallback = null
        }

        showStartupView("안전 데이터 준비 중…")
        startupExecutor.execute {
            runCatching {
                val reference = ReferenceAssetInstaller(this).install()
                val personalDatabase = File(filesDir, "personal.sqlite")
                val encryptedPersonalDatabase = File(filesDir, "personal.sqlite.enc")
                val vault = PersonalDatabaseVault(
                    personalDatabase,
                    encryptedPersonalDatabase,
                    ::personalDatabaseKey,
                )
                MedicineBridge(reference.database, personalDatabase, vault)
            }.onSuccess { bridge ->
                runOnUiThread {
                    if (!isFinishing && !isDestroyed) setupWebView(bridge)
                }
            }.onFailure { error ->
                runOnUiThread {
                    if (!isFinishing && !isDestroyed) {
                        showStartupView("앱 데이터를 준비하지 못했습니다.\n${error.message ?: "알 수 없는 오류"}")
                    }
                }
            }
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
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(
                    webView: WebView,
                    filePathCallback: ValueCallback<Array<Uri>>,
                    fileChooserParams: FileChooserParams,
                ): Boolean {
                    fileChooserCallback?.onReceiveValue(null)
                    fileChooserCallback = filePathCallback
                    return runCatching {
                        fileChooserLauncher.launch(fileChooserParams.createIntent())
                        true
                    }.getOrElse {
                        fileChooserCallback = null
                        filePathCallback.onReceiveValue(null)
                        false
                    }
                }
            }
            loadUrl(APP_URL)
        }
        webView = view
        setContentView(view)
    }

    private fun isAllowedOrigin(uri: Uri): Boolean =
        uri.scheme == "https" && uri.host == APP_ASSET_DOMAIN

    private fun showStartupView(message: String) {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(48, 48, 48, 48)
            addView(ProgressBar(this@MainActivity))
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
        }
        setContentView(root)
    }

    override fun onDestroy() {
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = null
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

    companion object {
        private const val APP_ASSET_DOMAIN = "appassets.androidplatform.net"
        private const val APP_URL = "https://$APP_ASSET_DOMAIN/static/index.html"
        private const val PERSONAL_DB_KEY_ALIAS = "medicine.personal-db.v1"
    }
}
