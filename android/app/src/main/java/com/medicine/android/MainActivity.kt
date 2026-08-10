package com.medicine.android

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts

class MainActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var fileChooserLauncher: ActivityResultLauncher<Intent>
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null

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

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest
                ): Boolean = !isAllowedOrigin(request.url.toString())

                @Suppress("DEPRECATION")
                override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean =
                    !isAllowedOrigin(url)
            }
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(
                    webView: WebView,
                    filePathCallback: ValueCallback<Array<Uri>>,
                    fileChooserParams: FileChooserParams
                ): Boolean {
                    this@MainActivity.fileChooserCallback?.onReceiveValue(null)
                    this@MainActivity.fileChooserCallback = filePathCallback
                    return runCatching {
                        fileChooserLauncher.launch(fileChooserParams.createIntent())
                        true
                    }.getOrElse {
                        this@MainActivity.fileChooserCallback = null
                        filePathCallback.onReceiveValue(null)
                        false
                    }
                }
            }
            loadUrl(BuildConfig.MEDICINE_WEB_URL)
        }
        setContentView(webView)
    }

    private fun isAllowedOrigin(url: String): Boolean =
        origin(url) != null && origin(url) == origin(BuildConfig.MEDICINE_WEB_URL)

    private fun origin(value: String): String? {
        val parsed = runCatching { Uri.parse(value) }.getOrNull() ?: return null
        val scheme = parsed.scheme?.lowercase()?.takeIf { it == "http" || it == "https" } ?: return null
        val host = parsed.host?.lowercase() ?: return null
        val defaultPort = if (scheme == "https") 443 else 80
        val port = parsed.port.takeIf { it >= 0 } ?: defaultPort
        return "$scheme://$host:$port"
    }

    override fun onDestroy() {
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = null
        if (::webView.isInitialized) {
            webView.stopLoading()
            webView.clearHistory()
            webView.removeAllViews()
            webView.destroy()
        }
        super.onDestroy()
    }
}
