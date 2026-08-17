package com.medicine.android

import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class ManifestContractTest {
    @Test
    fun nativeReferenceUpdaterHasInternetButWebViewRemainsLocalAndCleartextDisabled() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val index = java.io.File("../../medicine_app/static/index.html").readText()
        assertTrue(manifest.contains("android.permission.INTERNET"))
        assertFalse(manifest.contains("usesCleartextTraffic"))
        assertTrue(manifest.contains("com.chaquo.python.android.PyApplication"))
        assertTrue(activity.contains("blocked external request"))
        assertTrue(activity.contains("BuildConfig.REFERENCE_UPDATE_BASE_URL"))
        assertTrue(index.contains("connect-src 'self'"))
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
        assertTrue(activity.contains("WebChromeClient"))
        assertTrue(activity.contains("MediaStore.ACTION_IMAGE_CAPTURE"))
        assertTrue(activity.contains("FileProvider.getUriForFile"))
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("androidx.core.content.FileProvider"))
        assertTrue(manifest.contains("@xml/file_paths"))
    }

    @Test
    fun productShellPackagesAndRoutesOnDeviceOcrAssets() {
        val build = java.io.File("build.gradle.kts").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val index = java.io.File("../../medicine_app/static/index.html").readText()
        assertTrue(build.contains("PrepareOcrAssets"))
        assertTrue(build.contains("MEDICINE_OCR_ASSETS_DIR"))
        assertTrue(activity.contains("/ocr-assets/"))
        assertTrue(index.contains("ocr-image-input"))
        assertTrue(index.contains("ocr-review.js"))
    }

    @Test
    fun referenceDataInstallIsHashVerifiedAtomicAndReadOnly() {
        val installer = java.io.File("src/main/java/com/medicine/android/ReferenceAssetInstaller.kt").readText()
        val store = java.io.File("src/main/java/com/medicine/android/ReferenceStore.kt").readText()
        assertTrue(store.contains("MessageDigest.getInstance(\"SHA-256\")"))
        assertTrue(store.contains("highestActivatedSequence"))
        assertTrue(store.contains("pending"))
        assertTrue(store.contains("previous"))
        assertTrue(installer.contains("output.fd.sync()"))
        assertTrue(store.contains("candidate.renameTo(target)"))
        assertTrue(store.contains("target.setReadOnly()"))
        assertFalse(installer.contains("file.name.startsWith(\"mobile-\") && file.extension == \"sqlite\" && file != target"))
    }

    @Test
    fun referenceUpdaterPackagesSharedPatchCoreAndKeepsDistributionEndpointOffByDefault() {
        val build = java.io.File("build.gradle.kts").readText()
        val updater = java.io.File("src/main/java/com/medicine/android/ReferenceUpdater.kt").readText()
        val source = java.io.File("src/main/java/com/medicine/android/ReferenceReleaseHttpSource.kt").readText()
        assertTrue(build.contains("MEDICINE_REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("include(\"medicine_canonical/release.py\")"))
        assertTrue(updater.contains("ReferenceUpdateStatus.STAGED"))
        assertTrue(updater.contains(".artifact-"))
        assertTrue(source.contains("Range"))
        assertTrue(source.contains("Content-Range"))
        assertTrue(source.contains("HttpsURLConnection"))
    }

    @Test
    fun personalDatabaseIsSealedWithAndroidKeystoreBetweenBridgeRequests() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val bridge = java.io.File("src/main/java/com/medicine/android/MedicineBridge.kt").readText()
        val vault = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseVault.kt").readText()
        assertTrue(activity.contains("personal.sqlite.enc"))
        assertTrue(activity.contains("AndroidKeyStore"))
        assertTrue(bridge.contains("openForUse"))
        assertTrue(bridge.contains("prepare_for_seal"))
        assertTrue(bridge.contains("sealAfterUse"))
        assertTrue(vault.contains("AES/GCM/NoPadding"))
    }

    @Test
    fun pythonBridgeUsesCurrentTwoArgumentContractAndResealsOnInitFailure() {
        val bridge = java.io.File("src/main/java/com/medicine/android/MedicineBridge.kt").readText()
        assertFalse(
            bridge.contains(
                "personalDatabase.absolutePath,\n                referenceDatabase.absolutePath"
            )
        )
        assertTrue(bridge.contains("finally"))
        assertTrue(bridge.contains("vault.sealAfterUse()"))
    }

    @Test
    fun applicationUsesProductNameAndDoesNotExposeStartupExceptionDetails() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val strings = java.io.File("src/main/res/values/strings.xml").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()

        assertTrue(manifest.contains("android:label=\"@string/app_name\""))
        assertTrue(strings.contains("<string name=\"app_name\">약봄</string>"))
        assertFalse(strings.contains(">Medicine</string>"))
        assertTrue(activity.contains("Log.e(TAG, \"Application startup failed\", error)"))
        assertTrue(activity.contains("앱 데이터를 준비하지 못했습니다.\\n앱을 다시 실행해주세요."))
        assertFalse(activity.contains("\${error.message"))
    }

    @Test
    fun applicationDefinesLauncherIcon() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val icon = java.io.File("src/main/res/drawable/ic_launcher.xml")
        assertTrue(manifest.contains("android:icon=\"@drawable/ic_launcher\""))
        assertTrue(icon.isFile)
    }

    @Test
    fun healthDataIsExcludedFromCloudBackupAndDeviceTransfer() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val modernRules = java.io.File("src/main/res/xml/data_extraction_rules.xml")
        val legacyRules = java.io.File("src/main/res/xml/backup_rules.xml")

        assertTrue(manifest.contains("android:dataExtractionRules=\"@xml/data_extraction_rules\""))
        assertTrue(manifest.contains("android:fullBackupContent=\"@xml/backup_rules\""))
        assertTrue(modernRules.isFile)
        assertTrue(legacyRules.isFile)
        assertTrue(modernRules.readText().contains("<device-transfer>"))
        assertTrue(modernRules.readText().contains("<exclude domain=\"file\" path=\".\""))
        assertTrue(legacyRules.readText().contains("<exclude domain=\"file\" path=\".\""))
    }
}
