package com.medicine.android

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

data class InstalledReference(
    val database: File,
    val datasetId: String,
    val version: ReferenceVersion,
    val store: ReferenceStore,
    val referenceDir: File,
    val recoveryReason: String? = null,
)

class ReferenceAssetInstaller(
    private val context: Context,
    private val databaseVerifier: ReferenceDatabaseVerifier = PythonReferenceDatabaseVerifier(),
) {
    fun install(): InstalledReference {
        val manifestText = context.assets.open(MANIFEST_ASSET).bufferedReader().use { it.readText() }
        val manifest = JSONObject(manifestText)
        val bundled = ReferenceVersion(
            datasetId = manifest.getString("dataset_id"),
            sha256 = manifest.getString("sha256"),
            sizeBytes = manifest.getLong("size_bytes"),
            schemaVersion = manifest.getString("schema_version"),
            releaseSequence = 0,
        )

        val referenceDir = File(context.filesDir, "reference").apply {
            check(exists() || mkdirs()) { "cannot create reference data directory" }
        }
        val store = ReferenceStore(
            referenceDir,
            AtomicFileReferenceStateStorage(File(referenceDir, STATE_FILE)),
            databaseVerifier,
        )
        val selected = store.openForStartup(bundled, ::copyBundledAsset)
        return InstalledReference(
            database = selected.file,
            datasetId = selected.version.datasetId,
            version = selected.version,
            store = store,
            referenceDir = referenceDir,
            recoveryReason = selected.recoveryReason,
        )
    }

    private fun copyBundledAsset(target: File) {
        context.assets.open(DATABASE_ASSET).use { input ->
            FileOutputStream(target).use { output ->
                val buffer = ByteArray(1024 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    if (read > 0) output.write(buffer, 0, read)
                }
                output.fd.sync()
            }
        }
    }

    companion object {
        private const val DATABASE_ASSET = "mobile.sqlite"
        private const val MANIFEST_ASSET = "mobile.manifest.json"
        private const val STATE_FILE = "state.v1"
    }
}
