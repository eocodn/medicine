import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.file.FileSystemOperations
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction
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

val mobileDatabaseFile = rootProject.file("../data/db/mobile.sqlite")
val mobileManifestFile = rootProject.file("../data/db/mobile.manifest.json")
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

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
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
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.webkit:webkit:1.13.0")
    testImplementation("junit:junit:4.13.2")
}
