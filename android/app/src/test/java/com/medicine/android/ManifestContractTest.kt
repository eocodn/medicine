package com.medicine.android

import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class ManifestContractTest {
    @Test
    fun manifestContractRequiresNetworkAndDebugOnlyCleartextPlaceholder() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("android.permission.INTERNET"))
        assertTrue(manifest.contains("${'$'}{allowCleartext}"))
        assertTrue(manifest.contains("android:usesCleartextTraffic=\"${'$'}{allowCleartext}\""))
    }

    @Test
    fun androidShellUsesTheSharedBrowserOcrOnly() {
        val build = java.io.File("build.gradle.kts").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        assertFalse(build.contains("mlkit", ignoreCase = true))
        assertFalse(activity.contains("MedicineNative"))
        assertTrue(activity.contains("onShowFileChooser"))
    }
}
