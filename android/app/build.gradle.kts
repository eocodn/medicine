plugins {
    id("com.android.application")
}

android {
    namespace = "com.medicine.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.medicine.android"
        minSdk = 23
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        val configuredUrl = providers.gradleProperty("medicineWebUrl")
            .orElse("http://10.0.2.2:18787/")
        buildConfigField("String", "MEDICINE_WEB_URL", "\"${configuredUrl.get()}\"")
        manifestPlaceholders["allowCleartext"] = "false"
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    buildTypes {
        debug {
            manifestPlaceholders["allowCleartext"] = "true"
        }
        release {
            manifestPlaceholders["allowCleartext"] = "false"
        }
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.core:core-ktx:1.16.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.webkit:webkit:1.15.0")
    implementation("com.google.android.gms:play-services-mlkit-document-scanner:16.0.0")
    implementation("com.google.mlkit:text-recognition:16.0.1")
    implementation("com.google.mlkit:text-recognition-korean:16.0.1")
    testImplementation("junit:junit:4.13.2")
}
