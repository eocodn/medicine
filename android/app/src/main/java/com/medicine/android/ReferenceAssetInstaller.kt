package com.medicine.android

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest

data class InstalledReference(
    val database: File,
    val datasetId: String,
)

class ReferenceAssetInstaller(private val context: Context) {
    fun install(): InstalledReference {
        val manifestText = context.assets.open(MANIFEST_ASSET).bufferedReader().use { it.readText() }
        val manifest = JSONObject(manifestText)
        val expectedHash = manifest.getString("sha256").lowercase()
        val expectedSize = manifest.getLong("size_bytes")
        val datasetId = manifest.getString("dataset_id")
        require(expectedHash.matches(Regex("[0-9a-f]{64}"))) { "invalid mobile data hash" }
        require(expectedSize > 0) { "invalid mobile data size" }

        val referenceDir = File(context.filesDir, "reference").apply {
            check(exists() || mkdirs()) { "cannot create reference data directory" }
        }
        val target = File(referenceDir, "mobile-${expectedHash.take(16)}.sqlite")

        if (!isVerified(target, expectedHash, expectedSize)) {
            installVerifiedCopy(target, expectedHash, expectedSize)
        }
        check(isVerified(target, expectedHash, expectedSize)) { "reference data install failed" }
        check(target.setReadOnly()) { "cannot make reference data read-only" }

        referenceDir.listFiles { file ->
            file.name.startsWith("mobile-") && file.extension == "sqlite" && file != target
        }?.forEach { it.delete() }

        return InstalledReference(target, datasetId)
    }

    private fun isVerified(file: File, expectedHash: String, expectedSize: Long): Boolean =
        file.isFile && file.length() == expectedSize && sha256(file) == expectedHash

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                if (read > 0) digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun installVerifiedCopy(target: File, expectedHash: String, expectedSize: Long) {
        val temporary = File(target.parentFile, target.name + ".tmp")
        temporary.delete()
        val digest = MessageDigest.getInstance("SHA-256")
        var copied = 0L
        try {
            context.assets.open(DATABASE_ASSET).use { input ->
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        if (read == 0) continue
                        output.write(buffer, 0, read)
                        digest.update(buffer, 0, read)
                        copied += read
                    }
                    output.fd.sync()
                }
            }
            check(copied == expectedSize) { "reference data size mismatch" }
            val actualHash = digest.digest().joinToString("") { "%02x".format(it) }
            check(actualHash == expectedHash) { "reference data hash mismatch" }
            target.delete()
            check(temporary.renameTo(target)) { "cannot atomically install reference data" }
        } finally {
            temporary.delete()
        }
    }

    companion object {
        private const val DATABASE_ASSET = "mobile.sqlite"
        private const val MANIFEST_ASSET = "mobile.manifest.json"
    }
}
