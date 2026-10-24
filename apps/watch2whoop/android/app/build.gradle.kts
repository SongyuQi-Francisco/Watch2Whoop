// Watch2Whoop Android app (#253): first build for Huawei phones on HarmonyOS 4.x and older (Android compatible, no
// Google services), installed as an APK. Identity and signing follow the iOS app's xcconfig idea: the repository holds
// placeholders only (../Watch2Whoop.properties); your own values go in ../Watch2Whoop.local.properties (git-ignored)
// or in environment variables (the GitHub Actions build). Steps: apps/watch2whoop/README.md, "Android".
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Placeholders first, then the local file, then the environment (CI): later ones win.
val w2w = Properties().apply {
    listOf("Watch2Whoop.properties", "Watch2Whoop.local.properties").forEach { name ->
        val f = rootProject.file(name)
        if (f.exists()) f.inputStream().use { load(it) }
    }
}

fun w2wValue(key: String): String? =
    System.getenv(key)?.takeIf { it.isNotBlank() } ?: w2w.getProperty(key)?.takeIf { it.isNotBlank() }

// Release signing: a fixed keystore, so a new APK installs over the old one (family members update without losing their
// login). Without it a release build is signed with the debug key and can only be installed fresh; set
// W2W_REQUIRE_RELEASE_SIGNING=true (the tag build in CI does) to fail instead.
val releaseKeystore = w2wValue("W2W_ANDROID_KEYSTORE")?.let { file(it) }
val hasReleaseSigning = releaseKeystore != null && releaseKeystore.exists() &&
    listOf("W2W_ANDROID_KEYSTORE_PASSWORD", "W2W_ANDROID_KEY_ALIAS", "W2W_ANDROID_KEY_PASSWORD").all { w2wValue(it) != null }
if (!hasReleaseSigning && System.getenv("W2W_REQUIRE_RELEASE_SIGNING") == "true") {
    throw GradleException("Release signing required but W2W_ANDROID_KEYSTORE / its passwords / alias are missing")
}

android {
    // The Kotlin package of MainActivity; the installed app's ID is applicationId below.
    namespace = "org.watch2whoop.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = w2wValue("W2W_APPLICATION_ID") ?: "org.watch2whoop.app"
        // 29 = Android 10: the Open Wearables plugin's minimum (HarmonyOS 2 and newer report 29 or higher)
        minSdk = maxOf(flutter.minSdkVersion, 29)
        targetSdk = flutter.targetSdkVersion
        // From pubspec.yaml (version: X.Y.Z+N): versionName X.Y.Z, versionCode N, shared with iOS (docs/apps.md, section 4)
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        manifestPlaceholders["w2wDisplayName"] = w2wValue("W2W_DISPLAY_NAME") ?: "Watch2Whoop"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = releaseKeystore
                storePassword = w2wValue("W2W_ANDROID_KEYSTORE_PASSWORD")
                keyAlias = w2wValue("W2W_ANDROID_KEY_ALIAS")
                keyPassword = w2wValue("W2W_ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName(if (hasReleaseSigning) "release" else "debug")
            // No code shrinking in this first build: the plugins (Open Wearables SDK, ML Kit) are not tested with R8
            // here, and a sideloaded APK's size matters little.
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

dependencies {
    // FileProvider for photos / videos taken from the Today page's file inputs (MainActivity.kt)
    implementation("androidx.core:core:1.13.1")
}

flutter {
    source = "../.."
}
