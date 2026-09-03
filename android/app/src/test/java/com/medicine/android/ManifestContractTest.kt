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
    fun referenceDataBootstrapsThroughTheNativeSignedChannelRuntime() {
        val nativeReference = java.io.File(
            "src/main/java/com/medicine/android/ReferenceNativeCore.kt"
        ).readText()
        assertTrue(nativeReference.contains("nativeCreateReferenceRuntime"))
        assertTrue(nativeReference.contains("nativeReferencePrepare"))
        assertTrue(nativeReference.contains("nativeReferenceStart"))
        assertTrue(nativeReference.contains("nativeReferenceCheckForUpdate"))
        assertTrue(nativeReference.contains("BuildConfig.REFERENCE_TRUST_MANIFEST_JSON"))
    }

    @Test
    fun referenceUpdatesUseTheSameRustLifecycleRuntimeAsBootstrap() {
        val build = java.io.File("build.gradle.kts").readText()
        val bridge = java.io.File("src/main/java/com/medicine/android/ReferenceBootstrapJsBridge.kt").readText()
        val nativeReference = java.io.File(
            "src/main/java/com/medicine/android/ReferenceNativeCore.kt"
        ).readText()

        assertTrue(build.contains("MEDICINE_REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("REFERENCE_UPDATE_BASE_URL"))
        assertTrue(build.contains("pub-539f06de795a469c85ab40570a8634a2.r2.dev"))
        assertTrue(bridge.contains("ReferenceNativeCore.checkForUpdate(runtimeHandle)"))
        assertTrue(nativeReference.contains("nativeReferenceCheckForUpdate"))
    }

    @Test
    fun personalDatabaseIsSealedWithAndroidKeystoreBetweenBridgeRequests() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val personalApi = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseApi.kt").readText()
        val keyStore = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseKeyStore.kt").readText()
        val vault = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseVault.kt").readText()
        assertTrue(activity.contains("personal.sqlite.enc"))
        assertTrue(keyStore.contains("AndroidKeyStore"))
        assertTrue(personalApi.contains("vault.openForUse()"))
        assertTrue(personalApi.contains("nativeCore.prepareForSeal()"))
        assertTrue(personalApi.contains("vault.sealAfterUse()"))
        assertTrue(vault.contains("AES/GCM/NoPadding"))
    }

    @Test
    fun personalSchemaAndCheckpointUseRustInsideTheVaultCoordinator() {
        val personalApi = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseApi.kt").readText()
        val nativeCore = java.io.File("src/main/java/com/medicine/android/MedicineNativeCore.kt").readText()

        assertTrue(nativeCore.contains("fun initializePersonalDatabase"))
        assertTrue(nativeCore.contains("fun prepareForSeal"))
        assertTrue(nativeCore.contains("nativeInitializePersonalDatabase"))
        assertTrue(nativeCore.contains("nativePrepareForSeal"))
        assertFalse(personalApi.contains("prepare_for_seal"))

        val nativeCalls = listOf("nativeCore", "createdNativeCore")
        for (method in listOf("initializePersonalDatabase", "prepareForSeal")) {
            val call = nativeCalls
                .map { "$it.$method()" }
                .firstOrNull { personalApi.contains(it) }
            assertTrue("PersonalDatabaseApi must call native $method", call != null)
            val callIndex = personalApi.indexOf(call!!)
            val vaultOpen = personalApi.lastIndexOf("vault.openForUse()", callIndex)
            val vaultSeal = personalApi.indexOf("vault.sealAfterUse()", callIndex)
            assertTrue("$method must be inside the vault coordinator", vaultOpen >= 0)
            assertTrue("$method must run before vault reseal", vaultSeal > callIndex)
        }
    }

    @Test
    fun nativeBridgeHasNoPythonFallbackAndResealsOnInitFailure() {
        val personalApi = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseApi.kt").readText()
        assertFalse(personalApi.contains("Python"))
        assertFalse(personalApi.contains("callAttr"))
        assertTrue(personalApi.contains("finally"))
        assertTrue(personalApi.contains("vault.sealAfterUse()"))
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
        assertTrue(bootstrapUi.contains("network_failed"))
        assertTrue(bootstrapUi.contains("install_failed"))
        assertTrue(bootstrapUi.contains("안전 데이터 설치 또는 검증에 실패했습니다. 다시 시도해주세요."))
        assertFalse(bootstrapUi.contains("error.message"))
    }

    @Test
    fun webViewCompatibilityIsCheckedBeforeTheSharedUiLoads() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()

        assertTrue(activity.contains("WebView.getCurrentWebViewPackage()"))
        assertTrue(activity.contains("WebViewCompatibility.isSupportedVersion"))
        assertTrue(activity.contains("WebViewCompatibility.CAPABILITY_PROBE"))
        assertTrue(activity.contains("WebViewCompatibility.capabilitiesSatisfied"))
        assertTrue(activity.indexOf("WebViewCompatibility.isSupportedVersion") < activity.indexOf("setupWebView()"))
        assertTrue(activity.indexOf("WebViewCompatibility.capabilitiesSatisfied") < activity.indexOf("loadUrl(APP_URL)"))
    }

    @Test
    fun firstLaunchReferenceDownloadUsesSharedBlockingUiWithByteProgress() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val bootstrapBridge = java.io.File("src/main/java/com/medicine/android/ReferenceBootstrapJsBridge.kt").readText()
        val bootstrapUi = java.io.File("../../ui/src/reference-bootstrap.ts").readText()
        assertTrue(activity.contains("MedicineBootstrapNative"))
        assertTrue(activity.contains("MedicineNativeProxy"))
        assertTrue(activity.contains("WebSettings.LOAD_NO_CACHE"))
        assertFalse(activity.contains("AlertDialog"))
        assertTrue(bootstrapBridge.contains("ReferenceNativeCore.createReferenceRuntime"))
        assertTrue(bootstrapBridge.contains("ReferenceNativeCore.status(runtimeHandle)"))
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
    @Test
    fun medicationRemindersUseOrdinaryIdleAwareAlarmScheduling() {
        val manifest = java.io.File("src/main/AndroidManifest.xml").readText()
        val bridge = java.io.File("src/main/java/com/medicine/android/MedicineBridge.kt").readText()
        val personalApi = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseApi.kt")
        val keyStore = java.io.File("src/main/java/com/medicine/android/PersonalDatabaseKeyStore.kt")
        val scheduler = java.io.File("src/main/java/com/medicine/android/ReminderScheduler.kt")
        val alarmReceiver = java.io.File("src/main/java/com/medicine/android/ReminderAlarmReceiver.kt")
        val actionReceiver = java.io.File("src/main/java/com/medicine/android/ReminderActionReceiver.kt")
        val schedulerText = scheduler.readText()

        assertTrue(manifest.contains("android.permission.POST_NOTIFICATIONS"))
        assertTrue(manifest.contains("android.permission.RECEIVE_BOOT_COMPLETED"))
        assertTrue(manifest.contains(".ReminderAlarmReceiver"))
        assertTrue(manifest.contains(".ReminderActionReceiver"))
        assertTrue(manifest.contains(".ReminderSystemReceiver"))
        assertTrue(personalApi.isFile)
        assertTrue(keyStore.isFile)
        assertTrue(scheduler.isFile)
        assertTrue(alarmReceiver.isFile)
        assertTrue(actionReceiver.isFile)
        assertTrue(bridge.contains("PersonalDatabaseApi"))
        assertFalse(bridge.contains("vault.openForUse()"))
        assertTrue(schedulerText.contains("setAndAllowWhileIdle"))
    }

    @Test
    fun medicationReminderStartupAndChannelStateRecoverAuthoritatively() {
        val activity = java.io.File("src/main/java/com/medicine/android/MainActivity.kt").readText()
        val permissions = java.io.File(
            "src/main/java/com/medicine/android/ReminderPermissions.kt"
        ).readText()
        val notifications = java.io.File(
            "src/main/java/com/medicine/android/ReminderNotifications.kt"
        ).readText()

        val bridgeAttach = activity.indexOf("reminderNativeBridge = reminderBridge")
        val initialRefresh = activity.indexOf("reminderBridge.refresh()", bridgeAttach)
        assertTrue("cold launch must rebuild reminder alarms once the bridge exists", bridgeAttach >= 0)
        assertTrue("cold launch must refresh after reminder bridge attachment", initialRefresh > bridgeAttach)
        assertTrue(permissions.contains("getNotificationChannel(ReminderNotifications.CHANNEL_ID)"))
        assertTrue(permissions.contains("NotificationManager.IMPORTANCE_NONE"))
        assertTrue(notifications.contains("internal const val CHANNEL_ID"))
    }

}
