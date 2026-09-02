package com.medicine.android

import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class ManifestContractTest {
    @Test
    fun ocrIsAnUnconditionalPackagedProductCapability() {
        val build = java.io.File("build.gradle.kts").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val integration = java.io.File("src/main/java/com/medicine/android/ProductCapabilityIntegration.java").readText()
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val runtimeManifest = java.io.File("src/main/assets/ocr-assets/runtime-manifest.json")
        assertFalse(build.contains("MEDICINE_OCR_ASSETS_DIR"))
        assertFalse(activity.contains("/ocr-assets/"))
        assertTrue(integration.contains("/ocr-assets/"))
        assertTrue(manifest.contains("FileProvider"))
        assertTrue(runtimeManifest.isFile)
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
        val bootstrapBridge = java.io.File("src/main/java/com/medicine/android/ReferenceBootstrapJsBridge.kt").readText()
        val updater = java.io.File("src/main/java/com/medicine/android/ReferenceUpdater.kt").readText()
        val coordinator = java.io.File("src/main/java/com/medicine/android/ReferenceOperationCoordinator.kt").readText()
        val source = java.io.File("src/main/java/com/medicine/android/ReferenceReleaseHttpSource.kt").readText()
        assertTrue(build.contains("MEDICINE_REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("pub-539f06de795a469c85ab40570a8634a2.r2.dev"))
        assertFalse(build.contains("medicine_canonical"))
        assertTrue(bootstrapper.contains("RustReferenceDatabaseVerifier()"))
        assertTrue(activity.contains("RustReferenceArtifactRebuilder()"))
        assertTrue(bootstrapBridge.contains("\"rebuild\", \"rebuild-checkpoint\", \"verify-and-install\""))
        val nativeReference = java.io.File(
            "src/main/java/com/medicine/android/ReferenceNativeCore.kt"
        ).readText()
        assertTrue(nativeReference.contains("nativeVerifyManifest"))
        assertTrue(nativeReference.contains("nativeParseReleaseRoot"))
        assertTrue(nativeReference.contains("nativeVerifyDatabase"))
        assertTrue(nativeReference.contains("nativeRebuildArtifact"))
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
        assertTrue(bridge.contains("nativeCore.prepareForSeal()"))
        assertTrue(bridge.contains("sealAfterUse"))
        assertTrue(vault.contains("AES/GCM/NoPadding"))
    }

    @Test
    fun personalSchemaAndCheckpointUseRustInsideTheVaultCoordinator() {
        val bridge = java.io.File("src/main/java/com/medicine/android/MedicineBridge.kt").readText()
        val nativeCore = java.io.File("src/main/java/com/medicine/android/MedicineNativeCore.kt").readText()

        assertTrue(nativeCore.contains("fun initializePersonalDatabase"))
        assertTrue(nativeCore.contains("fun prepareForSeal"))
        assertTrue(nativeCore.contains("nativeInitializePersonalDatabase"))
        assertTrue(nativeCore.contains("nativePrepareForSeal"))
        assertFalse(bridge.contains("prepare_for_seal"))

        val nativeCalls = listOf("nativeCore", "createdNativeCore")
        for (method in listOf("initializePersonalDatabase", "prepareForSeal")) {
            val call = nativeCalls
                .map { "$it.$method()" }
                .firstOrNull { bridge.contains(it) }
            assertTrue("MedicineBridge must call native $method", call != null)
            val callIndex = bridge.indexOf(call!!)
            val vaultOpen = bridge.lastIndexOf("vault.openForUse()", callIndex)
            val vaultSeal = bridge.indexOf("vault.sealAfterUse()", callIndex)
            assertTrue("$method must be inside the vault coordinator", vaultOpen >= 0)
            assertTrue("$method must run before vault reseal", vaultSeal > callIndex)
        }
    }

    @Test
    fun nativeBridgeHasNoPythonFallbackAndResealsOnInitFailure() {
        val bridge = java.io.File("src/main/java/com/medicine/android/MedicineBridge.kt").readText()
        assertFalse(bridge.contains("Python"))
        assertFalse(bridge.contains("callAttr"))
        assertTrue(bridge.contains("finally"))
        assertTrue(bridge.contains("vault.sealAfterUse()"))
    }

    @Test
    fun applicationUsesProductNameAndSharedBootstrapShell() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val strings = java.io.File("src/main/res/values/strings.xml").readText()
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val bootstrapUi = java.io.File("../../ui/src/reference-bootstrap.ts").readText()

        assertTrue(manifest.contains("android:label=\"@string/app_name\""))
        assertTrue(strings.contains("<string name=\"app_name\">약봄</string>"))
        assertFalse(strings.contains(">Medicine</string>"))
        assertTrue(activity.contains("ReferenceBootstrapJsBridge"))
        assertTrue(bootstrapUi.contains("안전 데이터 준비에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해주세요."))
        assertFalse(bootstrapUi.contains("error.message"))
    }

    @Test
    fun firstLaunchReferenceDownloadUsesSharedBlockingUiWithByteProgress() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val bootstrapUi = java.io.File("../../ui/src/reference-bootstrap.ts").readText()
        assertTrue(activity.contains("MedicineBootstrapNative"))
        assertTrue(activity.contains("MedicineNativeProxy"))
        assertTrue(activity.contains("WebSettings.LOAD_NO_CACHE"))
        assertFalse(activity.contains("AlertDialog"))
        assertTrue(bootstrapUi.contains("다운로드하지 않으면 앱을 사용할 수 없습니다"))
        assertTrue(bootstrapUi.contains("completed_bytes"))
        assertTrue(bootstrapUi.contains("total_bytes"))
        assertTrue(bootstrapUi.contains("reference-bootstrap-progress"))
        assertTrue(bootstrapUi.contains("/api/development/reference-bootstrap/start"))
    }


    @Test
    fun androidBackDelegatesToOpenSharedModalBeforeActivityExit() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val dialog = java.io.File("../../ui/src/dialog.ts").readText()
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
