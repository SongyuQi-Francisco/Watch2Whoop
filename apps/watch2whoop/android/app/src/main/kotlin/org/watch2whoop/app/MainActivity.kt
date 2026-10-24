// Watch2Whoop on Android (#253): what the iOS AppDelegate / WKWebView give the Flutter side, for the Android WebView.
// - "watch2whoop/native": setup links (watch2whoop://setup?…) kept until Dart takes them, like LinkInbox on iOS
//   (`takeLinks`, and `linksPending` when one arrives while the app runs). Every other method of the iOS side
//   (HealthKit, sync log) answers notImplemented, which Dart already treats as "not available".
// - "watch2whoop/android": the microphone permission for hold-to-talk, this app's page in system settings, and the
//   file picker for the Today page's file inputs (lib/src/android_bridge.dart).
package org.watch2whoop.app

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.MediaStore
import android.provider.Settings
import android.webkit.MimeTypeMap
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterActivity() {
    private var native: MethodChannel? = null
    private val pendingLinks = mutableListOf<String>()
    private var micResult: MethodChannel.Result? = null
    private var pick: PendingPick? = null

    /** A file input waiting for the picker / camera. */
    private class PendingPick(
        val accept: List<String>,
        val multiple: Boolean,
        val capture: Boolean,
        val photo: Boolean,
        val video: Boolean,
        val result: MethodChannel.Result,
    ) {
        var cameraFile: File? = null
        var cameraUri: Uri? = null
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        native = MethodChannel(messenger, "watch2whoop/native").also { ch ->
            ch.setMethodCallHandler { call, result ->
                if (call.method == "takeLinks") {
                    result.success(ArrayList(pendingLinks))
                    pendingLinks.clear()
                } else {
                    result.notImplemented()
                }
            }
        }
        MethodChannel(messenger, "watch2whoop/android").setMethodCallHandler { call, result -> onAndroidCall(call, result) }
        keepLink(intent) // cold start from a link; Dart takes it on start (SetupLinks.start)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (keepLink(intent)) native?.invokeMethod("linksPending", null)
    }

    private fun keepLink(intent: Intent?): Boolean {
        if (intent == null) return false
        val data = intent.data ?: return false
        if (intent.action != Intent.ACTION_VIEW || data.scheme != "watch2whoop") return false
        pendingLinks.add(data.toString())
        intent.data = null // not again when the activity is recreated
        return true
    }

    private fun granted(permission: String) = checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    private fun onAndroidCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "hasMic" -> result.success(packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE))
            "requestMic" -> when {
                granted(Manifest.permission.RECORD_AUDIO) -> result.success(true)
                micResult != null -> result.success(false) // one dialog at a time
                else -> {
                    micResult = result
                    requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQUEST_MIC)
                }
            }
            "openAppSettings" -> {
                try {
                    startActivity(
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                } catch (e: ActivityNotFoundException) {
                    // nothing to open
                }
                result.success(null)
            }
            "pickFiles" -> startPick(call, result)
            else -> result.notImplemented()
        }
    }

    // ------------------------------------------------------------------------------------------------ file inputs

    private fun startPick(call: MethodCall, result: MethodChannel.Result) {
        if (pick != null) {
            result.success(emptyList<String>()) // one picker at a time
            return
        }
        val p = PendingPick(
            accept = call.argument<List<String>>("accept") ?: emptyList(),
            multiple = call.argument<Boolean>("multiple") ?: false,
            capture = call.argument<Boolean>("capture") ?: false,
            photo = call.argument<Boolean>("photo") ?: false,
            video = call.argument<Boolean>("video") ?: false,
            result = result,
        )
        pick = p
        cleanCameraFiles()
        // The camera permission is asked only for a camera button (`capture`); the gallery picker offers the camera
        // next to it once it is allowed (the setup QR scanner asks for it too).
        if (p.capture && !granted(Manifest.permission.CAMERA)) {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), REQUEST_CAMERA)
            return
        }
        launchPick(p, granted(Manifest.permission.CAMERA))
    }

    private fun launchPick(p: PendingPick, cameraAllowed: Boolean) {
        // One camera output can be tracked: a photo when the input takes photos, else a video.
        val camera = when {
            !cameraAllowed -> null
            p.photo -> cameraIntent(MediaStore.ACTION_IMAGE_CAPTURE, ".jpg", p)
            p.video -> cameraIntent(MediaStore.ACTION_VIDEO_CAPTURE, ".mp4", p)
            else -> null
        }
        val intent = if (p.capture && camera != null) {
            camera
        } else {
            val mimes = mimeTypes(p.accept)
            val content = Intent(Intent.ACTION_GET_CONTENT).apply {
                addCategory(Intent.CATEGORY_OPENABLE)
                type = if (mimes.size == 1) mimes[0] else "*/*"
                if (mimes.size > 1) putExtra(Intent.EXTRA_MIME_TYPES, mimes.toTypedArray())
                putExtra(Intent.EXTRA_ALLOW_MULTIPLE, p.multiple)
            }
            Intent.createChooser(content, null).apply {
                if (camera != null) putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(camera))
            }
        }
        try {
            startActivityForResult(intent, REQUEST_PICK)
        } catch (e: ActivityNotFoundException) {
            finishPick(emptyList())
        }
    }

    private fun cameraIntent(action: String, ext: String, p: PendingPick): Intent? = try {
        val dir = File(cacheDir, "camera").apply { mkdirs() }
        val file = File(dir, "w2w-${System.currentTimeMillis()}$ext")
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        p.cameraFile = file
        p.cameraUri = uri
        Intent(action).apply {
            putExtra(MediaStore.EXTRA_OUTPUT, uri)
            addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            clipData = ClipData.newRawUri(null, uri) // the write grant also reaches a camera opened from the chooser
        }
    } catch (e: IllegalArgumentException) {
        null
    }

    // Accept entries (image types, `.csv` …) → MIME types the system pickers understand (unknown extensions dropped)
    private fun mimeTypes(accept: List<String>): List<String> = accept.mapNotNull { a ->
        when {
            a.startsWith(".") -> MimeTypeMap.getSingleton().getMimeTypeFromExtension(a.substring(1))
            a.contains("/") -> a
            else -> null
        }
    }.distinct()

    /** Camera files of earlier picks; the page has read them long since (older than an hour). */
    private fun cleanCameraFiles() {
        val cutoff = System.currentTimeMillis() - 60 * 60 * 1000L
        File(cacheDir, "camera").listFiles()?.forEach { if (it.lastModified() < cutoff) it.delete() }
    }

    private fun finishPick(uris: List<String>) {
        val p = pick ?: return
        pick = null
        p.result.success(uris)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_PICK) return
        val p = pick ?: return
        val uris = mutableListOf<String>()
        if (resultCode == Activity.RESULT_OK) {
            val clip = data?.clipData
            val single = data?.data
            if (clip != null && clip.itemCount > 0 && clip.getItemAt(0).uri != p.cameraUri) {
                for (i in 0 until clip.itemCount) clip.getItemAt(i).uri?.let { uris.add(it.toString()) }
            } else if (single != null && single != p.cameraUri) {
                uris.add(single.toString())
            } else {
                // The camera wrote to our file (it returns no data with EXTRA_OUTPUT)
                val f = p.cameraFile
                val u = p.cameraUri
                if (f != null && u != null && f.length() > 0) uris.add(u.toString())
            }
        }
        finishPick(uris)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val ok = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
        when (requestCode) {
            REQUEST_MIC -> {
                micResult?.success(ok)
                micResult = null
            }
            REQUEST_CAMERA -> pick?.let { launchPick(it, ok) } // declined: the gallery picker without the camera
        }
    }

    private companion object {
        // Distinct from the plugins' request codes (FlutterActivity forwards every result to them as well)
        const val REQUEST_MIC = 0x5701
        const val REQUEST_CAMERA = 0x5702
        const val REQUEST_PICK = 0x5703
    }
}
