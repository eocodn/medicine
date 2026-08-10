package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OcrBridgeContractTest {
    private val contract = OcrBridgeContract(setOf("http://10.0.2.2:18787"))

    @Test
    fun originAllowlistAndSequenceAreStrict() {
        val rejected = contract.begin("https://evil.example/")
        assertEquals(OcrState.FAILED, rejected.state)
        assertEquals("ORIGIN_NOT_ALLOWED", rejected.errorCode)

        val started = contract.begin("http://10.0.2.2:18787/path")
        assertEquals(OcrState.SCANNING, started.state)
        assertEquals(1L, started.sequence)
        val recognized = contract.markRecognizing(started.operationId)
        assertEquals(2L, recognized.sequence)
    }

    @Test
    fun concurrentAndStaleOperationsCannotCommit() {
        val started = contract.begin("http://10.0.2.2:18787")
        val busy = contract.begin("http://10.0.2.2:18787")
        assertEquals("OCR_BUSY", busy.errorCode)
        assertEquals(started.operationId, busy.operationId)
        assertEquals(OcrState.SCANNING, contract.current()?.state)

        val stale = contract.ready("ocr-old", OcrHints(productQueries = listOf("hidden")))
        assertEquals("STALE_OPERATION", stale.errorCode)
        assertTrue(contract.current()?.hints == null)
    }

    @Test
    fun cancelAndTimeoutRemainTerminalFailures() {
        val started = contract.begin("http://10.0.2.2:18787")
        val cancelled = contract.cancel(started.operationId)
        assertEquals(OcrState.CANCELLED, cancelled.state)
        val late = contract.fail(started.operationId, "TIMEOUT")
        assertEquals("STALE_OPERATION", late.errorCode)

        val next = contract.begin("http://10.0.2.2:18787")
        val failed = contract.fail(next.operationId, "TIMEOUT")
        assertEquals(OcrState.FAILED, failed.state)
        assertEquals("TIMEOUT", failed.errorCode)
    }

    @Test
    fun expiredOperationIsFailedAndCannotBeReusedByLateCallback() {
        var now = 1_000L
        val timed = OcrBridgeContract(setOf("http://10.0.2.2:18787"), clock = { now }, timeoutMillis = 100)
        val started = timed.begin("http://10.0.2.2:18787", "web-op")
        now = 1_101L
        val timeout = timed.timeoutIfExpired()
        assertEquals(OcrState.FAILED, timeout?.state)
        assertEquals("TIMEOUT", timeout?.errorCode)
        assertEquals("STALE_OPERATION", timed.ready("web-op", OcrHints(productQueries = listOf("late"))).errorCode)

        val reused = timed.begin("http://10.0.2.2:18787", "web-op")
        assertEquals(OcrState.FAILED, reused.state)
        assertEquals("OPERATION_REUSED", reused.errorCode)
        assertEquals(started.operationId, reused.operationId)
    }

    @Test
    fun busyStartIsAnEventOnlyAndCannotLaunchAnotherScanner() {
        val started = contract.begin("http://10.0.2.2:18787", "first")
        val busy = contract.begin("http://10.0.2.2:18787", "second")
        assertEquals(started.operationId, busy.operationId)
        assertEquals("OCR_BUSY", busy.errorCode)
        assertTrue(!busy.launchScanner)
    }

    @Test
    fun webMessagePolicyRejectsSubframesAndForeignOrigins() {
        val policy = WebBridgePolicy(setOf("http://10.0.2.2:18787"))
        assertTrue(policy.accepts(isMainFrame = true, sourceOrigin = "http://10.0.2.2:18787"))
        assertTrue(!policy.accepts(isMainFrame = false, sourceOrigin = "http://10.0.2.2:18787"))
        assertTrue(!policy.accepts(isMainFrame = true, sourceOrigin = "https://evil.example"))
    }
}
