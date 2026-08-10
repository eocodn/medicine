package com.medicine.android

import org.junit.Assert.assertTrue
import org.junit.Test

class ManifestContractTest {
    @Test
    fun manifestContractRequiresNetworkAndDebugOnlyCleartextPlaceholder() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("android.permission.INTERNET"))
        assertTrue(manifest.contains("${'$'}{allowCleartext}"))
        assertTrue(manifest.contains("android:usesCleartextTraffic=\"${'$'}{allowCleartext}\""))
    }
}
