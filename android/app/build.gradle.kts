import org.gradle.api.tasks.Sync

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val mobileDatabase = rootProject.file("../data/db/mobile.sqlite")
val mobileManifest = rootProject.file("../data/db/mobile.manifest.json")
val generatedMobileAssets = layout.buildDirectory.dir("generated/mobileAssets").get().asFile
val ocrAssetsDir = providers.environmentVariable("MEDICINE_BROWSER_OCR_ASSETS")
    .orElse("/opt/medicine-browser-ocr")

val prepareMobileAssets = tasks.register<Sync>("prepareMobileAssets") {
    doFirst {
        check(mobileDatabase.isFile) {
            "Missing $mobileDatabase. Build the verified mobile snapshot before assembling Android."
        }
        check(mobileManifest.isFile) {
            "Missing $mobileManifest. Build the verified mobile snapshot before assembling Android."
        }
        check(file(ocrAssetsDir.get()).isDirectory) {
            "Missing browser OCR assets: ${ocrAssetsDir.get()}"
        }
    }
    from(mobileDatabase)
    from(mobileManifest)
    into(generatedMobileAssets)
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

    sourceSets.getByName("main") {
        assets.srcDirs(
            rootProject.file("../medicine_app/static"),
            file(ocrAssetsDir.get()),
            generatedMobileAssets,
        )
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

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach {
    dependsOn(prepareMobileAssets)
}
tasks.matching { it.name.contains("Lint", ignoreCase = true) }.configureEach {
    dependsOn(prepareMobileAssets)
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.webkit:webkit:1.13.0")
    testImplementation("junit:junit:4.13.2")
}
