package com.medicine.android

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WebViewCompatibilityTest {
    @Test
    fun webView93IsTheMinimumSupportedRuntime() {
        assertFalse(WebViewCompatibility.isSupportedVersion(null))
        assertFalse(WebViewCompatibility.isSupportedVersion(""))
        assertFalse(WebViewCompatibility.isSupportedVersion("92.0.4515.166"))
        assertFalse(WebViewCompatibility.isSupportedVersion("not-a-version"))
        assertTrue(WebViewCompatibility.isSupportedVersion("93.0.4577.82"))
        assertTrue(WebViewCompatibility.isSupportedVersion("138.0.7204.179"))
    }

    @Test
    fun capabilityProbeCoversTheSharedUiRuntimeFloor() {
        val probe = WebViewCompatibility.CAPABILITY_PROBE

        assertTrue(probe.contains("value?.nested"))
        assertTrue(probe.contains("value ??= 2"))
        assertTrue(probe.contains("Object.hasOwn"))
        assertTrue(probe.contains("Array.prototype.at"))
        assertTrue(probe.contains("String.prototype.replaceAll"))
        assertTrue(probe.contains("crypto.randomUUID"))
        assertTrue(probe.contains("WebAssembly.validate"))
    }

    @Test
    fun onlyAnExplicitSuccessfulCapabilityProbeAllowsStartup() {
        assertTrue(WebViewCompatibility.capabilitiesSatisfied("true"))
        assertFalse(WebViewCompatibility.capabilitiesSatisfied("false"))
        assertFalse(WebViewCompatibility.capabilitiesSatisfied("null"))
        assertFalse(WebViewCompatibility.capabilitiesSatisfied(null))
    }
}