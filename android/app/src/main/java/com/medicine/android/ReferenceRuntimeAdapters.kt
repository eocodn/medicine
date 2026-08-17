package com.medicine.android

import com.chaquo.python.Python
import java.io.File
import java.io.FileOutputStream
import java.util.zip.GZIPInputStream

class PythonReferenceDatabaseVerifier : ReferenceDatabaseVerifier {
    override fun verify(file: File, version: ReferenceVersion) {
        val result = Python.getInstance()
            .getModule("medicine_app.reference_update")
            .callAttr(
                "verify_reference_database",
                file.absolutePath,
                version.schemaVersion,
                version.datasetId,
            )
        require(result.callAttr("get", "status").toString() == "verified") {
            "reference runtime database was not verified"
        }
    }
}

class PythonReferenceArtifactRebuilder : ReferenceArtifactRebuilder {
    override fun rebuild(
        current: InstalledReferenceVersion,
        artifact: ReferenceReleaseArtifact,
        downloaded: File,
        output: File,
    ) {
        output.delete()
        output.parentFile?.let { parent ->
            check(parent.exists() || parent.mkdirs()) { "cannot create reference rebuild directory" }
        }
        when (artifact.kind) {
            ReferenceArtifactKind.FULL_GZIP -> decompressFull(downloaded, output)
            ReferenceArtifactKind.CHUNK_PATCH -> Python.getInstance()
                .getModule("medicine_canonical.release")
                .callAttr(
                    "apply_chunk_patch",
                    current.file.absolutePath,
                    downloaded.absolutePath,
                    output.absolutePath,
                )
        }
    }

    private fun decompressFull(source: File, output: File) {
        val temporary = File(output.parentFile, output.name + ".decompressing")
        temporary.delete()
        try {
            GZIPInputStream(source.inputStream().buffered()).use { input ->
                FileOutputStream(temporary).use { raw ->
                    val buffer = ByteArray(1024 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        if (read > 0) raw.write(buffer, 0, read)
                    }
                    raw.fd.sync()
                }
            }
            check(temporary.renameTo(output)) { "cannot atomically finish reference decompression" }
        } finally {
            temporary.delete()
        }
    }
}
