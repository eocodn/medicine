package com.medicine.android

import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResult
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.google.mlkit.common.MlKitException
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.documentscanner.GmsDocumentScanner
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.korean.KoreanTextRecognizerOptions
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var scanner: GmsDocumentScanner
    private lateinit var scannerLauncher: ActivityResultLauncher<IntentSenderRequest>
    private lateinit var contract: OcrBridgeContract
    private lateinit var bridgePolicy: WebBridgePolicy
    private val mainHandler = Handler(Looper.getMainLooper())
    private var activeOperationId: String? = null
    private var activeEpoch = 0L
    private var scannerInFlight = false
    private var deadlineRunnable: Runnable? = null
    private var activeRecognizers: RecognizerPair? = null
    private var activeTextParts: MutableList<String>? = null

    private data class RecognizerPair(
        val latin: com.google.mlkit.vision.text.TextRecognizer,
        val korean: com.google.mlkit.vision.text.TextRecognizer
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val configuredOrigin = OcrBridgeContract.normalizeOrigin(BuildConfig.MEDICINE_WEB_URL)
        contract = OcrBridgeContract(configuredOrigin?.let(::setOf) ?: emptySet())
        bridgePolicy = WebBridgePolicy(configuredOrigin?.let(::setOf) ?: emptySet())
        scanner = GmsDocumentScanning.getClient(
            GmsDocumentScannerOptions.Builder()
                .setGalleryImportAllowed(false)
                .setPageLimit(2)
                .setResultFormats(GmsDocumentScannerOptions.RESULT_FORMAT_JPEG)
                .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_FULL)
                .build()
        )
        scannerLauncher = registerForActivityResult(
            ActivityResultContracts.StartIntentSenderForResult(),
            ::handleScanResult
        )
        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                    !isAllowedOrigin(request.url.toString())

                override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean =
                    !isAllowedOrigin(url)
            }
            if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
                WebViewCompat.addWebMessageListener(
                    this,
                    "MedicineNative",
                    configuredOrigin?.let(::setOf) ?: emptySet()
                ) { _, message, sourceOrigin, isMainFrame, _ ->
                    if (!bridgePolicy.accepts(isMainFrame, sourceOrigin.toString())) return@addWebMessageListener
                    handleNativeMessage(message.data.orEmpty())
                }
            }
            loadUrl(BuildConfig.MEDICINE_WEB_URL)
        }
        setContentView(webView)
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            emitCapabilities(supported = false)
        }
    }

    private fun handleScanResult(result: ActivityResult) {
        val operationId = activeOperationId ?: return
        val epoch = activeEpoch
        if (!scannerInFlight || !isCurrentOperation(operationId, epoch)) return
        scannerInFlight = false
        if (result.resultCode != RESULT_OK || result.data == null) {
            val cancelled = contract.cancel(operationId)
            invalidateOperation(epoch)
            emit(cancelled)
            return
        }
        val scanResult = GmsDocumentScanningResult.fromActivityResultIntent(result.data)
        val pages = scanResult?.pages.orEmpty()
        if (pages.isEmpty() || pages.size > 2) {
            val failed = contract.fail(operationId, "INVALID_SCAN_RESULT")
            invalidateOperation(epoch)
            emit(failed)
            return
        }
        emit(contract.markRecognizing(operationId))
        recognizePages(operationId, epoch, pages.map { it.imageUri }, 0, mutableListOf())
    }

    private fun isAllowedOrigin(url: String): Boolean =
        OcrBridgeContract.normalizeOrigin(url)?.let { it in contractAllowedOrigins() } == true

    private fun contractAllowedOrigins(): Set<String> =
        OcrBridgeContract.normalizeOrigin(BuildConfig.MEDICINE_WEB_URL)?.let(::setOf) ?: emptySet()

    private fun recognizePages(
        operationId: String,
        epoch: Long,
        uris: List<Uri>,
        index: Int,
        textParts: MutableList<String>
    ) {
        if (!isCurrentOperation(operationId, epoch)) {
            textParts.clear()
            return
        }
        activeTextParts = textParts
        if (index >= uris.size) {
            val hints = OcrHintParser.parse(textParts.joinToString("\n"))
            textParts.clear()
            activeTextParts = null
            val ready = contract.ready(operationId, hints)
            invalidateOperation(epoch)
            emit(ready)
            return
        }
        val image = try {
            // ML Kit consumes the scanner-provided content URI directly. No
            // copy, path lookup, bitmap, or file is created by this app.
            InputImage.fromFilePath(this, uris[index])
        } catch (_: Exception) {
            textParts.clear()
            val failed = contract.fail(operationId, "IMAGE_URI_UNREADABLE")
            invalidateOperation(epoch)
            emit(failed)
            return
        }
        val pair = RecognizerPair(
            TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS),
            TextRecognition.getClient(KoreanTextRecognizerOptions.Builder().build())
        )
        activeRecognizers = pair
        pair.latin.process(image)
            .addOnSuccessListener { latinText ->
                if (!isCurrentOperation(operationId, epoch)) {
                    closeRecognizers(pair)
                    textParts.clear()
                    return@addOnSuccessListener
                }
                textParts.add(latinText.text)
                pair.korean.process(image)
                    .addOnSuccessListener { koreanText ->
                        if (!isCurrentOperation(operationId, epoch)) {
                            closeRecognizers(pair)
                            textParts.clear()
                            return@addOnSuccessListener
                        }
                        textParts.add(koreanText.text)
                        closeRecognizers(pair)
                        recognizePages(operationId, epoch, uris, index + 1, textParts)
                    }
                    .addOnFailureListener {
                        closeRecognizers(pair)
                        if (!isCurrentOperation(operationId, epoch)) {
                            textParts.clear()
                            return@addOnFailureListener
                        }
                        textParts.clear()
                        val failed = contract.fail(operationId, "KOREAN_RECOGNITION_FAILED")
                        invalidateOperation(epoch)
                        emit(failed)
                    }
            }
            .addOnFailureListener { error ->
                closeRecognizers(pair)
                if (!isCurrentOperation(operationId, epoch)) {
                    textParts.clear()
                    return@addOnFailureListener
                }
                textParts.clear()
                val failure = if (error is MlKitException && error.errorCode == MlKitException.UNSUPPORTED) {
                    contract.unsupported(operationId)
                } else {
                    contract.fail(operationId, "LATIN_RECOGNITION_FAILED")
                }
                invalidateOperation(epoch)
                emit(failure)
            }
    }

    private fun emit(envelope: OcrEnvelope) {
        val event = JSONObject()
            .put("schema_version", envelope.schemaVersion)
            .put("operation_id", envelope.operationId)
            .put("sequence", envelope.sequence)
            // The web contract models unsupported hardware as a failed
            // operation with a machine-readable GMS_UNSUPPORTED code.
            .put("state", if (envelope.state == OcrState.UNSUPPORTED) "FAILED" else envelope.state.name)
        envelope.errorCode?.let { event.put("error_code", it) }
        envelope.hints?.let { hints ->
            event.put("ambiguity_codes", JSONArray(hints.ambiguityCodes))
            event.put("unsupported_codes", JSONArray(hints.unsupportedCodes))
            event.put("hints", JSONObject()
                .put("schema_version", hints.schemaVersion)
                .put("product_queries", JSONArray(hints.productQueries))
                .put("dose_quantity", hints.doseQuantity)
                .put("dose_unit", hints.doseUnit)
                .put("frequency_per_day", hints.frequencyPerDay)
                .put("duration_days", hints.durationDays)
                .put("times", JSONArray(hints.times))
                .put("ambiguity_codes", JSONArray(hints.ambiguityCodes))
                .put("unsupported_codes", JSONArray(hints.unsupportedCodes)))
        }
        val script = "window.__medicineOcrEvent && window.__medicineOcrEvent(${event})"
        webView.post { webView.evaluateJavascript(script, null) }
    }

    private fun handleNativeMessage(message: String) {
        val request = runCatching { JSONObject(message) }.getOrNull() ?: return
        if (request.optInt("schema_version", -1) != 1) return
        when (request.optString("command")) {
            "get_capabilities" -> emitCapabilities()
            "start_scan" -> startScan(request.optString("operation_id").takeIf { it.isNotEmpty() })
            "cancel_scan" -> cancelScan(request.optString("operation_id"))
            "finish_scan" -> contract.finish(request.optString("operation_id"))
        }
    }

    private fun startScan(requestedOperationId: String?) {
        val started = contract.begin(webView.url.orEmpty(), requestedOperationId)
        emit(started)
        if (started.state != OcrState.SCANNING || !started.launchScanner || scannerInFlight) return
        activeOperationId = started.operationId
        activeEpoch += 1
        scannerInFlight = true
        val epoch = activeEpoch
        scheduleDeadline(epoch)
        scanner.getStartScanIntent(this@MainActivity)
            .addOnSuccessListener { sender ->
                if (!isCurrentOperation(started.operationId, epoch) || !scannerInFlight) return@addOnSuccessListener
                scannerLauncher.launch(IntentSenderRequest.Builder(sender).build())
            }
            .addOnFailureListener { error ->
                if (!isCurrentOperation(started.operationId, epoch)) return@addOnFailureListener
                scannerInFlight = false
                val failure = if (error is MlKitException && error.errorCode == MlKitException.UNSUPPORTED) {
                    contract.unsupported(started.operationId)
                } else {
                    contract.fail(started.operationId, "SCANNER_UNAVAILABLE")
                }
                invalidateOperation(epoch)
                emit(failure)
            }
    }

    private fun cancelScan(operationId: String) {
        val cancelled = contract.cancel(operationId)
        val epoch = activeEpoch
        if (cancelled.state == OcrState.CANCELLED && activeOperationId == operationId) invalidateOperation(epoch)
        emit(cancelled)
    }

    private fun scheduleDeadline(epoch: Long) {
        deadlineRunnable?.let(mainHandler::removeCallbacks)
        val timeout = Runnable {
            if (!isCurrentOperation(activeOperationId, epoch)) return@Runnable
            val failure = contract.timeoutIfExpired()
            invalidateOperation(epoch)
            failure?.let(::emit)
        }
        deadlineRunnable = timeout
        mainHandler.postDelayed(timeout, OcrBridgeContract.DEFAULT_TIMEOUT_MILLIS)
    }

    private fun isCurrentOperation(operationId: String?, epoch: Long): Boolean =
        operationId != null && operationId == activeOperationId && epoch == activeEpoch &&
            contract.current()?.operationId == operationId &&
            contract.current()?.state in setOf(OcrState.SCANNING, OcrState.RECOGNIZING)

    private fun closeRecognizers(pair: RecognizerPair? = activeRecognizers) {
        pair ?: return
        pair.latin.close()
        pair.korean.close()
        if (activeRecognizers === pair) activeRecognizers = null
    }

    private fun invalidateOperation(epoch: Long) {
        if (epoch != activeEpoch) return
        activeEpoch += 1
        scannerInFlight = false
        deadlineRunnable?.let(mainHandler::removeCallbacks)
        deadlineRunnable = null
        closeRecognizers()
        activeTextParts?.clear()
        activeTextParts = null
        activeOperationId = null
    }

    private fun emitCapabilities(supported: Boolean = true) {
        val event = JSONObject()
            .put("schema_version", 1)
            .put("capabilities", JSONObject()
                .put("supported", supported)
                .put("scanner", supported)
                .put("ocr", supported)
                .put("bundled_korean", supported)
                .put("bundled_latin", supported))
        val script = "window.__medicineOcrEvent && window.__medicineOcrEvent(${event})"
        webView.post { webView.evaluateJavascript(script, null) }
    }

    override fun onDestroy() {
        activeEpoch += 1
        deadlineRunnable?.let(mainHandler::removeCallbacks)
        deadlineRunnable = null
        closeRecognizers()
        activeTextParts?.clear()
        activeTextParts = null
        activeOperationId = null
        scannerInFlight = false
        if (::webView.isInitialized) {
            if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
                WebViewCompat.removeWebMessageListener(webView, "MedicineNative")
            }
            webView.stopLoading()
            webView.clearHistory()
            webView.removeAllViews()
            webView.destroy()
        }
        super.onDestroy()
    }
}
