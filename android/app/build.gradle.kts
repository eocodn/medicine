import org.gradle.api.DefaultTask
import org.gradle.api.artifacts.dsl.LockMode
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.FileSystemOperations
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.io.File
import java.net.URI
import java.security.MessageDigest
import java.util.Base64
import java.util.Properties
import javax.inject.Inject

abstract class PrepareOcrAssets : DefaultTask() {
    @get:Inject
    abstract val fileSystemOperations: FileSystemOperations

    @get:InputDirectory
    abstract val sourceDirectory: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @TaskAction
    fun prepare() {
        fileSystemOperations.sync {
            from(sourceDirectory)
            into(outputDirectory)
        }
    }
}

abstract class PrepareSharedUiAssets : DefaultTask() {
    @get:Inject
    abstract val execOperations: ExecOperations

    @get:Inject
    abstract val fileSystemOperations: FileSystemOperations

    @get:Input
    abstract val tscBinary: Property<String>

    @get:Input
    abstract val nodeBinary: Property<String>

    @get:Input
    abstract val ocrEnabled: Property<Boolean>

    @get:InputDirectory
    abstract val sourceDirectory: DirectoryProperty

    @get:InputDirectory
    abstract val publicDirectory: DirectoryProperty

    @get:InputFile
    abstract val tsconfigFile: RegularFileProperty

    @get:InputFile
    abstract val buildCapabilityScript: RegularFileProperty

    @get:InputFile
    abstract val buildConfigScript: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @TaskAction
    fun prepare() {
        fileSystemOperations.delete { delete(outputDirectory) }
        val preparedSource = temporaryDir.resolve("src")
        fileSystemOperations.delete { delete(preparedSource) }
        execOperations.exec {
            commandLine(
                nodeBinary.get(),
                "--experimental-strip-types",
                buildCapabilityScript.get().asFile.absolutePath,
                "prepare",
                sourceDirectory.get().asFile.absolutePath,
                preparedSource.absolutePath,
                if (ocrEnabled.get()) "enabled" else "disabled",
            )
        }
        execOperations.exec {
            commandLine(
                tscBinary.get(),
                "-p",
                preparedSource.resolve("tsconfig.json").absolutePath,
                "--outDir",
                outputDirectory.get().asFile.absolutePath,
            )
        }
        fileSystemOperations.copy {
            from(publicDirectory)
            into(outputDirectory)
        }
        execOperations.exec {
            commandLine(
                nodeBinary.get(),
                "--experimental-strip-types",
                buildConfigScript.get().asFile.absolutePath,
                outputDirectory.get().asFile.absolutePath,
                if (ocrEnabled.get()) "enabled" else "disabled",
            )
        }
    }
}

abstract class PrepareRustJniLibs : DefaultTask() {
    @get:Inject
    abstract val fileSystemOperations: FileSystemOperations

    @get:InputFile
    abstract val libraryFile: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @TaskAction
    fun prepare() {
        fileSystemOperations.sync {
            from(libraryFile)
            into(outputDirectory.dir("arm64-v8a"))
        }
    }
}

plugins {
    id("com.android.application")
}

val releaseVersionPropertiesFile = rootProject.file("release.properties")
val releaseVersionProperties = Properties().apply {
    releaseVersionPropertiesFile.inputStream().use(::load)
}
val releaseVersionName = releaseVersionProperties.getProperty("versionName")
    ?.trim()
    ?.takeIf { it.isNotEmpty() }
    ?: error("android/release.properties must define versionName")
val releaseVersionCode = releaseVersionProperties.getProperty("versionCode")
    ?.trim()
    ?.toIntOrNull()
    ?.takeIf { it > 0 }
    ?: error("android/release.properties must define a positive integer versionCode")

val referenceTrustManifestFile = rootProject.file("../deploy/reference-signing-trusted-keys.json")

fun decodeReviewedPublicKeyPem(pem: String, keyId: String): ByteArray {
    require(pem.startsWith("-----BEGIN PUBLIC KEY-----\n")) {
        "reference signing public key must use PUBLIC KEY PEM: $keyId"
    }
    require(pem.endsWith("-----END PUBLIC KEY-----\n")) {
        "reference signing public key must end with a newline: $keyId"
    }
    val body = pem
        .removePrefix("-----BEGIN PUBLIC KEY-----\n")
        .removeSuffix("-----END PUBLIC KEY-----\n")
        .replace("\n", "")
    return try {
        Base64.getDecoder().decode(body)
    } catch (error: IllegalArgumentException) {
        throw GradleException("reference signing public key is invalid base64: $keyId", error)
    }
}

fun referenceTrustedKeysJson(file: File): String {
    require(file.isFile) { "reference signing trust manifest is missing: $file" }
    val document = JsonSlurper().parse(file)
    require(document is Map<*, *> && document.keys == setOf("active_key_id", "keys")) {
        "reference signing trust manifest shape is invalid"
    }
    val activeKeyId = document["active_key_id"] as? String
        ?: error("reference signing trust manifest active_key_id is invalid")
    val rawKeys = document["keys"] as? List<*>
        ?: error("reference signing trust manifest keys must be a list")
    require(rawKeys.isNotEmpty()) { "reference signing trust manifest keys must not be empty" }

    val trusted = linkedMapOf<String, String>()
    rawKeys.forEach { rawKey ->
        require(
            rawKey is Map<*, *> &&
                rawKey.keys == setOf("key_id", "public_key_pem", "spki_sha256")
        ) { "reference signing trust entry shape is invalid" }
        val keyId = rawKey["key_id"] as? String
            ?: error("reference signing trust key ID is invalid")
        require(Regex("[A-Za-z0-9._-]{1,64}").matches(keyId)) {
            "reference signing trust key ID is invalid: $keyId"
        }
        require(!trusted.containsKey(keyId)) { "duplicate reference signing trust key ID: $keyId" }
        val pem = rawKey["public_key_pem"] as? String
            ?: error("reference signing public key is invalid: $keyId")
        val reviewedFingerprint = rawKey["spki_sha256"] as? String
            ?: error("reference signing fingerprint is invalid: $keyId")
        require(Regex("[0-9a-f]{64}").matches(reviewedFingerprint)) {
            "reference signing fingerprint is invalid: $keyId"
        }
        val spki = decodeReviewedPublicKeyPem(pem, keyId)
        val actualFingerprint = MessageDigest.getInstance("SHA-256")
            .digest(spki)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
        require(actualFingerprint == reviewedFingerprint) {
            "reference signing fingerprint does not match reviewed key: $keyId"
        }
        trusted[keyId] = spki.joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }
    require(activeKeyId in trusted) {
        "reference signing active key ID is missing from trusted keys"
    }
    return JsonOutput.toJson(trusted)
}

val referenceSigningTrustedKeysJson = referenceTrustedKeysJson(referenceTrustManifestFile)

data class AndroidReleaseEnvironment(
    val versionCode: Int,
    val versionName: String,
    val keystorePath: String,
    val keystorePassword: String,
    val keyAlias: String,
    val keyPassword: String,
)

val releaseEnvironmentNames = listOf(
    "MEDICINE_ANDROID_VERSION_CODE",
    "MEDICINE_ANDROID_VERSION_NAME",
    "MEDICINE_ANDROID_KEYSTORE_PATH",
    "MEDICINE_ANDROID_KEYSTORE_PASSWORD",
    "MEDICINE_ANDROID_KEY_ALIAS",
    "MEDICINE_ANDROID_KEY_PASSWORD",
)

fun requireReleaseEnvironment(): AndroidReleaseEnvironment {
    fun required(name: String): String = System.getenv(name)
        ?.takeIf { it.isNotEmpty() }
        ?: error("$name is required for Android release tasks")

    val versionCodeText = required("MEDICINE_ANDROID_VERSION_CODE")
    val versionCode = versionCodeText.toIntOrNull()
        ?: error("MEDICINE_ANDROID_VERSION_CODE must be a positive integer")
    require(versionCode > 0) { "MEDICINE_ANDROID_VERSION_CODE must be a positive integer" }
    require(versionCode == releaseVersionCode) {
        "MEDICINE_ANDROID_VERSION_CODE must match android/release.properties"
    }
    val versionName = required("MEDICINE_ANDROID_VERSION_NAME").trim()
    require(versionName.isNotEmpty()) { "MEDICINE_ANDROID_VERSION_NAME must not be blank" }
    require(versionName == releaseVersionName) {
        "MEDICINE_ANDROID_VERSION_NAME must match android/release.properties"
    }

    return AndroidReleaseEnvironment(
        versionCode = versionCode,
        versionName = versionName,
        keystorePath = required("MEDICINE_ANDROID_KEYSTORE_PATH"),
        keystorePassword = required("MEDICINE_ANDROID_KEYSTORE_PASSWORD"),
        keyAlias = required("MEDICINE_ANDROID_KEY_ALIAS"),
        keyPassword = required("MEDICINE_ANDROID_KEY_PASSWORD"),
    )
}

val releaseEnvironment = if (releaseEnvironmentNames.all { !System.getenv(it).isNullOrEmpty() }) {
    requireReleaseEnvironment()
} else {
    null
}

val verifyReleaseEnvironment = tasks.register("verifyReleaseEnvironment") {
    group = "verification"
    description = "Validates Android release version and signing inputs before release tasks run."
    doLast {
        val release = requireReleaseEnvironment()
        require(File(release.keystorePath).isFile) {
            "MEDICINE_ANDROID_KEYSTORE_PATH does not point to a readable file"
        }
        require(System.getenv("MEDICINE_OCR_ASSETS_DIR").isNullOrBlank()) {
            "OCR is not enabled for the current Android release"
        }
    }
}

// Gradle accepts abbreviated task names (for example, `assRel`), so guard the
// resolved release tasks rather than trying to infer intent from raw CLI names.
tasks.configureEach {
    if (name != verifyReleaseEnvironment.name && name.contains("Release", ignoreCase = true)) {
        dependsOn(verifyReleaseEnvironment)
    }
}

val referenceUpdateBaseUrlOverride = System.getenv("MEDICINE_REFERENCE_UPDATE_BASE_URL")?.trim()?.takeIf { it.isNotEmpty() }
val defaultReleaseReferenceUpdateBaseUrl = "https://pub-539f06de795a469c85ab40570a8634a2.r2.dev/"
val releaseReferenceUpdateBaseUrl = System.getenv("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL")
    ?.trim()
    ?.takeIf { it.isNotEmpty() }
    ?: defaultReleaseReferenceUpdateBaseUrl

fun validateReferenceUpdateBaseUrl(value: String) {
    if (value.isEmpty()) return
    val uri = URI(value)
    require(uri.scheme == "https" && !uri.host.isNullOrBlank() && uri.path.endsWith("/")) {
        "MEDICINE_REFERENCE_UPDATE_BASE_URL must be an HTTPS origin/base path ending in /"
    }
    require(uri.query == null && uri.fragment == null && '"' !in value && '\\' !in value) {
        "MEDICINE_REFERENCE_UPDATE_BASE_URL cannot contain query, fragment, quotes, or backslashes"
    }
}
validateReferenceUpdateBaseUrl(referenceUpdateBaseUrlOverride.orEmpty())
validateReferenceUpdateBaseUrl(releaseReferenceUpdateBaseUrl)
if (releaseReferenceUpdateBaseUrl.isNotEmpty()) {
    val releaseUri = URI(releaseReferenceUpdateBaseUrl)
    require(releaseUri.host.endsWith(".r2.dev")) {
        "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL must use the development r2.dev endpoint"
    }
}
val effectiveReferenceUpdateBaseUrl = referenceUpdateBaseUrlOverride ?: releaseReferenceUpdateBaseUrl

val ocrAssetsDirectory = providers.environmentVariable("MEDICINE_OCR_ASSETS_DIR")
    .orNull
    ?.trim()
    ?.takeIf { it.isNotEmpty() }
val isOcrEnabled = ocrAssetsDirectory != null
val prepareOcrAssets = ocrAssetsDirectory?.let { source ->
    tasks.register<PrepareOcrAssets>("prepareOcrAssets") {
        sourceDirectory.set(layout.dir(providers.provider { file(source) }))
        outputDirectory.set(layout.buildDirectory.dir("generated/ocrAssets"))
    }
}

val sharedUiRoot = rootProject.file("../ui")
val sharedUiTscBinary = providers.environmentVariable("MEDICINE_TSC_BINARY")
    .orElse(sharedUiRoot.resolve("node_modules/.bin/tsc").absolutePath)
val sharedUiNodeBinary = providers.environmentVariable("MEDICINE_NODE_BINARY").orElse("node")
val prepareSharedUiAssets = tasks.register<PrepareSharedUiAssets>("prepareSharedUiAssets") {
    tscBinary.set(sharedUiTscBinary)
    nodeBinary.set(sharedUiNodeBinary)
    ocrEnabled.set(isOcrEnabled)
    sourceDirectory.set(layout.dir(providers.provider { sharedUiRoot.resolve("src") }))
    publicDirectory.set(layout.dir(providers.provider { sharedUiRoot.resolve("public") }))
    tsconfigFile.set(layout.file(providers.provider { sharedUiRoot.resolve("tsconfig.json") }))
    buildCapabilityScript.set(layout.file(providers.provider { sharedUiRoot.resolve("build-capability.ts") }))
    buildConfigScript.set(layout.file(providers.provider { rootProject.file("../ui/build-config.ts") }))
    outputDirectory.set(layout.buildDirectory.dir("generated/sharedUiAssets"))
}

val rustNdkVersion = "29.0.14206865"
val rustTargetDirectory = layout.buildDirectory.dir("rust-target")
val rustLibrary = rustTargetDirectory.map {
    it.file("aarch64-linux-android/release/libmedicine_core.so")
}
val rustJniLibsDirectory = layout.buildDirectory.dir("generated/rustJniLibs")
val buildRustNative = tasks.register<Exec>("buildRustNative") {
    group = "build"
    description = "Builds the arm64 Rust application core for Android."
    workingDir(rootProject.file(".."))
    inputs.file(rootProject.file("../rust-toolchain.toml"))
    inputs.file(rootProject.file("../rust/medicine_core/Cargo.toml"))
    inputs.file(rootProject.file("../rust/medicine_core/Cargo.lock"))
    inputs.dir(rootProject.file("../rust/medicine_core/src"))
    outputs.file(rustLibrary)
    environment("MEDICINE_ANDROID_NDK_VERSION", rustNdkVersion)
    environment("MEDICINE_RUST_TARGET_DIR", rustTargetDirectory.get().asFile.absolutePath)
    commandLine("bash", rootProject.file("../scripts/build_android_rust.sh").absolutePath)
}
val prepareRustJniLibs = tasks.register<PrepareRustJniLibs>("prepareRustJniLibs") {
    dependsOn(buildRustNative)
    libraryFile.set(rustLibrary)
    outputDirectory.set(rustJniLibsDirectory)
}

android {
    namespace = "com.medicine.android"
    compileSdk = 36
    ndkVersion = rustNdkVersion

    defaultConfig {
        applicationId = "kr.yakbom.app"
        minSdk = 24
        targetSdk = 35
        versionCode = releaseEnvironment?.versionCode ?: releaseVersionCode
        versionName = releaseEnvironment?.versionName ?: releaseVersionName
        buildConfigField(
            "String",
            "REFERENCE_TRUSTED_KEYS_JSON",
            JsonOutput.toJson(referenceSigningTrustedKeysJson),
        )

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        create("release") {
            releaseEnvironment?.let { release ->
                storeFile = File(release.keystorePath)
                storePassword = release.keystorePassword
                keyAlias = release.keyAlias
                keyPassword = release.keyPassword
            }
        }
    }

    buildTypes {
        getByName("debug") {
            buildConfigField("String", "REFERENCE_UPDATE_BASE_URL", "\"$effectiveReferenceUpdateBaseUrl\"")
        }
        getByName("release") {
            buildConfigField("String", "REFERENCE_UPDATE_BASE_URL", "\"$effectiveReferenceUpdateBaseUrl\"")
            signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    buildFeatures {
        buildConfig = true
    }

    sourceSets.getByName("main").java.directories.add(
        if (isOcrEnabled) "src/ocr/java" else "src/noOcr/java"
    )
    sourceSets.getByName("main").manifest.srcFile(
        if (isOcrEnabled) "src/ocr/AndroidManifest.xml" else "src/main/AndroidManifest.xml"
    )
    if (isOcrEnabled) sourceSets.getByName("main").res.directories.add("src/ocr/res")
}

androidComponents {
    onVariants(selector().all()) { variant ->
        val assets = checkNotNull(variant.sources.assets) {
            "Android assets source API is unavailable for ${variant.name}"
        }
        assets.addGeneratedSourceDirectory(prepareSharedUiAssets, PrepareSharedUiAssets::outputDirectory)
        prepareOcrAssets?.let { task ->
            assets.addGeneratedSourceDirectory(task, PrepareOcrAssets::outputDirectory)
        }
        val jniLibs = checkNotNull(variant.sources.jniLibs) {
            "Android JNI source API is unavailable for ${variant.name}"
        }
        jniLibs.addGeneratedSourceDirectory(prepareRustJniLibs, PrepareRustJniLibs::outputDirectory)
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.webkit:webkit:1.13.0")
    testImplementation("junit:junit:4.13.2")
    // Android ships org.json at runtime; use the matching JVM implementation so
    // contract-manifest parsing is exercised by local unit tests instead of the
    // Android stub methods returning default values.
    testImplementation("org.json:json:20160810")
}

dependencyLocking {
    lockAllConfigurations()
    lockMode.set(LockMode.STRICT)
}
