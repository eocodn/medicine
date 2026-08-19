import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.file.FileSystemOperations
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction
import java.net.URI
import javax.inject.Inject

abstract class PrepareMobileAssets : DefaultTask() {
    @get:Inject
    abstract val fileSystemOperations: FileSystemOperations

    @get:InputFile
    abstract val mobileDatabase: RegularFileProperty

    @get:InputFile
    abstract val mobileManifest: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @TaskAction
    fun prepare() {
        fileSystemOperations.sync {
            from(mobileDatabase)
            from(mobileManifest)
            into(outputDirectory)
        }
    }
}

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

val mobileDatabaseOverride = System.getenv("MEDICINE_MOBILE_DB")?.takeIf { it.isNotBlank() }
val mobileManifestOverride = System.getenv("MEDICINE_MOBILE_MANIFEST")?.takeIf { it.isNotBlank() }
require((mobileDatabaseOverride == null) == (mobileManifestOverride == null)) {
    "Both MEDICINE_MOBILE_DB and MEDICINE_MOBILE_MANIFEST must be set together"
}
val mobileDatabaseFile = rootProject.file(mobileDatabaseOverride ?: "../data/db/mobile.sqlite")
val mobileManifestFile = rootProject.file(mobileManifestOverride ?: "../data/db/mobile.manifest.json")
val referenceUpdateBaseUrlOverride = System.getenv("MEDICINE_REFERENCE_UPDATE_BASE_URL")?.trim()?.takeIf { it.isNotEmpty() }
val releaseReferenceUpdateBaseUrl = System.getenv("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL")?.trim().orEmpty()

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
val debugReferenceUpdateBaseUrl = referenceUpdateBaseUrlOverride.orEmpty()
val releaseEffectiveReferenceUpdateBaseUrl = referenceUpdateBaseUrlOverride ?: releaseReferenceUpdateBaseUrl
val prepareMobileAssets = tasks.register<PrepareMobileAssets>("prepareMobileAssets") {
    mobileDatabase.set(layout.file(providers.provider { mobileDatabaseFile }))
    mobileManifest.set(layout.file(providers.provider { mobileManifestFile }))
    outputDirectory.set(layout.buildDirectory.dir("generated/mobileAssets"))
}

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
        versionCode = 1
        versionName = "0.2.0"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        getByName("debug") {
            buildConfigField("String", "REFERENCE_UPDATE_BASE_URL", "\"$debugReferenceUpdateBaseUrl\"")
        }
        getByName("release") {
            buildConfigField("String", "REFERENCE_UPDATE_BASE_URL", "\"$releaseEffectiveReferenceUpdateBaseUrl\"")
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
        assets.addGeneratedSourceDirectory(prepareMobileAssets, PrepareMobileAssets::outputDirectory)
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
        include("medicine_reference/**/*.py")
        include("medicine_reference/data/mfds_remark_registry.tsv")
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
}
