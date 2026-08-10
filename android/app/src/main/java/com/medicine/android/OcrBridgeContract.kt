package com.medicine.android

import java.net.URI
import java.util.Locale

enum class OcrState { IDLE, SCANNING, RECOGNIZING, READY, CANCELLED, FAILED, UNSUPPORTED }

/**
 * Owns the native/Web state boundary. Every accepted transition is scoped to
 * one operation and has a monotonically increasing sequence. Terminal states
 * cannot be revived; callers must explicitly start a new operation.
 */
class OcrBridgeContract(
    private val allowedOrigins: Set<String>,
    private val clock: () -> Long = System::currentTimeMillis,
    private val timeoutMillis: Long = DEFAULT_TIMEOUT_MILLIS
) {
    private var nextOperation = 0L
    private var active: OcrEnvelope? = null
    private var deadlineMillis: Long? = null
    private val issuedOperationIds = mutableSetOf<String>()

    @Synchronized
    fun begin(origin: String, requestedOperationId: String? = null): OcrEnvelope {
        val normalized = normalizeOrigin(origin)
        val current = active
        if (current != null && current.state in setOf(OcrState.SCANNING, OcrState.RECOGNIZING)) {
            val busy = current.copy(sequence = current.sequence + 1, errorCode = "OCR_BUSY", launchScanner = false)
            active = busy
            return busy
        }
        val requested = requestedOperationId?.takeIf { it.matches(Regex("[A-Za-z0-9._:-]{1,128}")) }
        val id = requested ?: "ocr-${++nextOperation}"
        if (!issuedOperationIds.add(id)) {
            val reused = OcrEnvelope(operationId = id, sequence = (current?.sequence ?: 0) + 1, state = OcrState.FAILED, errorCode = "OPERATION_REUSED", launchScanner = false)
            active = reused
            deadlineMillis = null
            return reused
        }
        if (normalized == null || normalized !in allowedOrigins) {
            val rejected = OcrEnvelope(operationId = id, sequence = 1, state = OcrState.FAILED, errorCode = "ORIGIN_NOT_ALLOWED", launchScanner = false)
            active = rejected
            deadlineMillis = null
            return rejected
        }
        val started = OcrEnvelope(operationId = id, sequence = 1, state = OcrState.SCANNING)
        active = started
        deadlineMillis = clock() + timeoutMillis
        return started
    }

    @Synchronized
    fun markRecognizing(operationId: String): OcrEnvelope = transition(operationId, OcrState.RECOGNIZING)

    @Synchronized
    fun ready(operationId: String, hints: OcrHints): OcrEnvelope = transition(operationId, OcrState.READY, hints = hints)

    @Synchronized
    fun unsupported(operationId: String, code: String = "GMS_UNSUPPORTED"): OcrEnvelope =
        transition(operationId, OcrState.UNSUPPORTED, errorCode = code)

    @Synchronized
    fun fail(operationId: String, code: String): OcrEnvelope = transition(operationId, OcrState.FAILED, errorCode = code)

    @Synchronized
    fun cancel(operationId: String): OcrEnvelope = transition(operationId, OcrState.CANCELLED, errorCode = "OCR_CANCELLED")

    @Synchronized
    fun current(): OcrEnvelope? = active

    @Synchronized
    fun timeoutIfExpired(now: Long = clock()): OcrEnvelope? {
        val current = active ?: return null
        val deadline = deadlineMillis ?: return null
        if (current.state !in setOf(OcrState.SCANNING, OcrState.RECOGNIZING) || now < deadline) return null
        return transition(current.operationId, OcrState.FAILED, errorCode = "TIMEOUT")
    }

    @Synchronized
    fun finish(operationId: String): Boolean {
        val current = active ?: return false
        if (current.operationId != operationId || current.state != OcrState.READY) return false
        active = null
        deadlineMillis = null
        return true
    }

    private fun transition(operationId: String, state: OcrState, hints: OcrHints? = null, errorCode: String? = null): OcrEnvelope {
        val current = active
        if (current == null || current.operationId != operationId || current.state !in setOf(OcrState.SCANNING, OcrState.RECOGNIZING)) {
            return OcrEnvelope(operationId = operationId, sequence = (current?.sequence ?: 0) + 1, state = OcrState.FAILED, errorCode = "STALE_OPERATION")
        }
        val next = current.copy(sequence = current.sequence + 1, state = state, hints = hints, errorCode = errorCode, launchScanner = false)
        active = next
        if (state !in setOf(OcrState.SCANNING, OcrState.RECOGNIZING)) deadlineMillis = null
        return next
    }

    companion object {
        const val DEFAULT_TIMEOUT_MILLIS = 90_000L

        fun normalizeOrigin(value: String): String? {
            val uri = runCatching { URI(value) }.getOrNull() ?: return null
            val scheme = uri.scheme?.lowercase(Locale.US) ?: return null
            val authority = uri.rawAuthority?.lowercase(Locale.US) ?: return null
            if (scheme !in setOf("http", "https") || authority.isBlank() || uri.userInfo != null) return null
            return "$scheme://$authority"
        }
    }
}

data class OcrEnvelope(
    val schemaVersion: Int = 1,
    val operationId: String,
    val sequence: Long,
    val state: OcrState,
    val hints: OcrHints? = null,
    val errorCode: String? = null,
    val launchScanner: Boolean = true
)

class WebBridgePolicy(private val allowedOrigins: Set<String>) {
    fun accepts(isMainFrame: Boolean, sourceOrigin: String): Boolean =
        isMainFrame && OcrBridgeContract.normalizeOrigin(sourceOrigin)?.let { it in allowedOrigins } == true
}
