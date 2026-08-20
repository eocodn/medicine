import org.gradle.api.DefaultTask
import org.gradle.api.artifacts.dsl.LockMode
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.FileSystemOperations
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction
import java.io.File
import java.net.URI
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

plugins {
    id("com.android.application")
    id("com.chaquo.python")
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
    .orElse("/opt/medicine-ocr-assets")
val prepareOcrAssets = tasks.register<PrepareOcrAssets>("prepareOcrAssets") {
    sourceDirectory.set(layout.dir(ocrAssetsDirectory.map { file(it) }))
    outputDirectory.set(layout.buildDirectory.dir("generated/ocrAssets"))
}

android {
    namespace = "com.medicine.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.medicine.android"
        minSdk = 24
        targetSdk = 35
        versionCode = releaseEnvironment?.versionCode ?: releaseVersionCode
        versionName = releaseEnvironment?.versionName ?: releaseVersionName

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

}

androidComponents {
    onVariants(selector().all()) { variant ->
        val assets = checkNotNull(variant.sources.assets) {
            "Android assets source API is unavailable for ${variant.name}"
        }
        assets.addStaticSourceDirectory(rootProject.file("../medicine_app/static").absolutePath)
        assets.addGeneratedSourceDirectory(prepareOcrAssets, PrepareOcrAssets::outputDirectory)
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        buildPython("python3.12")
    }
    sourceSets.getByName("main") {
        srcDir(rootProject.file(".."))
        include("medicine_app/**/*.py")
        exclude("medicine_app/cli.py")
        include("medicine_canonical/__init__.py")
        include("medicine_canonical/release.py")
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
