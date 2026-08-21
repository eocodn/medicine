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
    fun androidShellUsesLocalAssetsAndRustApiBridge() {
        val build = java.io.File("build.gradle.kts").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val bridge = java.io.File("src/main/java/com/medicine/android/MedicineBridge.kt").readText()
        val nativeCore = java.io.File("src/main/java/com/medicine/android/MedicineNativeCore.kt")
        val proguard = java.io.File("proguard-rules.pro").readText()
        assertFalse(build.contains("mlkit", ignoreCase = true))
        assertTrue(build.contains("com.chaquo.python"))
        assertTrue(build.contains("androidx.webkit:webkit"))
        assertTrue(build.contains("buildRustNative"))
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
        assertTrue(nativeCore.isFile)
        assertTrue(bridge.contains("MedicineNativeCore"))
        assertTrue(bridge.contains("MedicineNativeCore(referenceDatabase, personalDatabase)"))
        assertTrue(bridge.contains("nativeCore.requestAccess"))
        assertTrue(bridge.contains("nativeCore.handlesRequest"))
        assertTrue(bridge.contains("nativeCore.request"))
        assertFalse(bridge.contains("api.callAttr(\"request_access\""))
        assertTrue(proguard.contains("-keep class com.medicine.android.MedicineNativeCore"))
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
        assertTrue(index.contains("ocr-intake.js"))
    }

    @Test
    fun referenceDataBootstrapsFromSignedChannelWithoutBundledDatabaseAssets() {
        val bootstrapper = java.io.File("src/main/java/com/medicine/android/ReferenceBootstrapper.kt").readText()
        val store = java.io.File("src/main/java/com/medicine/android/ReferenceStore.kt").readText()
        val build = java.io.File("build.gradle.kts").readText()
        assertTrue(store.contains("MessageDigest.getInstance(\"SHA-256\")"))
        assertTrue(store.contains("highestActivatedSequence"))
        assertTrue(store.contains("pending"))
        assertTrue(store.contains("previous"))
        assertTrue(bootstrapper.contains("ReferenceManifestVerifier(ReferenceTrust.trustedPublicKeys)"))
        assertTrue(bootstrapper.contains(".bootstrap-artifact-"))
        assertTrue(bootstrapper.contains("ReferenceBootstrapStorageException"))
        assertTrue(store.contains("candidate.renameTo(target)"))
        assertTrue(store.contains("target.setReadOnly()"))
        assertFalse(java.io.File("src/main/java/com/medicine/android/ReferenceAssetInstaller.kt").exists())
        assertFalse(build.contains("PrepareMobileAssets"))
        assertFalse(build.contains("mobile.sqlite"))
        assertFalse(build.contains("mobile.manifest.json"))
    }

    @Test
    fun referenceUpdaterPackagesSharedPatchCoreAndUsesDevelopmentDistributionEndpoint() {
        val build = java.io.File("build.gradle.kts").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val bootstrapper = java.io.File("src/main/java/com/medicine/android/ReferenceBootstrapper.kt").readText()
        val updater = java.io.File("src/main/java/com/medicine/android/ReferenceUpdater.kt").readText()
        val coordinator = java.io.File("src/main/java/com/medicine/android/ReferenceOperationCoordinator.kt").readText()
        val source = java.io.File("src/main/java/com/medicine/android/ReferenceReleaseHttpSource.kt").readText()
        assertTrue(build.contains("MEDICINE_REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("pub-539f06de795a469c85ab40570a8634a2.r2.dev"))
        assertTrue(build.contains("include(\"medicine_canonical/release.py\")"))
        assertTrue(updater.contains("ReferenceUpdateStatus.STAGED"))
        assertTrue(updater.contains(".artifact-"))
        assertTrue(bootstrapper.contains("ReferenceOperationCoordinator.exclusive"))
        assertTrue(updater.contains("ReferenceOperationCoordinator.exclusive"))
        assertTrue(coordinator.contains("ReentrantLock"))
        assertTrue(activity.contains("RejectedExecutionException"))
        assertTrue(activity.contains("startupExecutor.isShutdown"))
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
        assertTrue(activity.contains("앱 데이터를 준비하지 못했습니다.\\n다시 시도해주세요."))
        assertTrue(activity.contains("다시 시도"))
        assertFalse(activity.contains("\${error.message"))
    }


    @Test
    fun androidBackDelegatesToOpenSharedModalBeforeActivityExit() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val dialog = java.io.File("../../medicine_app/static/dialog.js").readText()
        assertTrue(activity.contains("OnBackPressedCallback"))
        assertTrue(activity.contains("MedicineDialog?.handleNativeBack"))
        assertTrue(activity.contains("evaluateJavascript"))
        assertTrue(activity.contains("backDispatchGate.tryBegin()"))
        assertTrue(activity.contains("backDispatchGate.complete"))
        assertTrue(dialog.contains("function handleNativeBack"))
        assertTrue(dialog.contains("if (!activeSheet()) return false"))
    }
    @Test
    fun applicationDefinesLauncherIcon() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val icon = java.io.File("src/main/res/drawable/ic_launcher.xml")
        assertTrue(manifest.contains("android:icon=\"@drawable/ic_launcher\""))
        assertTrue(icon.isFile)
    }

    @Test
    fun lightAppThemeUsesReadableDarkStatusBarForeground() {
        val styles = java.io.File("src/main/res/values/styles.xml").readText()
        assertTrue(styles.contains("<item name=\"android:statusBarColor\">#F3F5F1</item>"))
        assertTrue(styles.contains("<item name=\"android:windowLightStatusBar\">true</item>"))
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
