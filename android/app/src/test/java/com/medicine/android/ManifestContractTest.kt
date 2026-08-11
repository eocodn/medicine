package com.medicine.android

import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class ManifestContractTest {
    @Test
    fun manifestDoesNotRequireExternalNetworkOrCleartext() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        assertFalse(manifest.contains("android.permission.INTERNET"))
        assertFalse(manifest.contains("usesCleartextTraffic"))
        assertTrue(manifest.contains("com.chaquo.python.android.PyApplication"))
    }

    @Test
    fun androidShellUsesLocalAssetsAndPythonApiBridge() {
        val build = java.io.File("build.gradle.kts").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        assertFalse(build.contains("mlkit", ignoreCase = true))
        assertTrue(build.contains("com.chaquo.python"))
        assertTrue(build.contains("androidx.webkit:webkit"))
        assertFalse(build.contains("medicineWebUrl"))
        assertFalse(activity.contains("MEDICINE_WEB_URL"))
        assertTrue(activity.contains("WebViewAssetLoader"))
        assertTrue(activity.contains("MedicineNative"))
        assertTrue(activity.contains("addJavascriptInterface"))
        assertTrue(activity.contains("onShowFileChooser"))
    }

    @Test
    fun browserOcrStillRunsInTheWebWorkerNotThroughTheNativeBridge() {
        val browserOcr = java.io.File("../../medicine_app/static/browser-ocr.js").readText()
        val ocr = java.io.File("../../medicine_app/static/ocr.js").readText()
        assertFalse(browserOcr.contains("MedicineNative"))
        assertFalse(ocr.contains("MedicineNative"))
        assertTrue(browserOcr.contains("/ocr-assets/direct/ocr-worker.js"))
    }

    @Test
    fun referenceDataInstallIsHashVerifiedAtomicAndReadOnly() {
        val installer = java.io.File("src/main/java/com/medicine/android/ReferenceAssetInstaller.kt").readText()
        assertTrue(installer.contains("MessageDigest.getInstance(\"SHA-256\")"))
        assertTrue(installer.contains("isVerified(target, expectedHash, expectedSize)"))
        assertTrue(installer.contains("output.fd.sync()"))
        assertTrue(installer.contains("temporary.renameTo(target)"))
        assertTrue(installer.contains("target.setReadOnly()"))
    }
}
