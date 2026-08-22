package com.medicine.android

import android.util.Base64
import org.json.JSONObject
import java.io.File

interface NativeReferenceArtifactObserver {
    fun progress(phase: String, completedBytes: Long, totalBytes: Long)
    fun checkpoint(path: String)
}

object ReferenceNativeCore {
    fun verifyManifest(
        trustedPublicKeys: Map<String, ByteArray>,
        envelopeVersion: Int,
        algorithm: String,
        keyId: String,
        releaseSequence: Long,
        payloadBase64: String,
        signatureBase64: String,
        minimumExclusiveSequence: Long?,
    ): VerifiedReferenceManifestSignature {
        val keys = JSONObject()
        trustedPublicKeys.forEach { (id, bytes) ->
            keys.put(id, bytes.joinToString("") { "%02x".format(it.toInt() and 0xff) })
        }
        val result = JSONObject(
            nativeVerifyManifest(
                envelopeVersion.toLong(),
                algorithm,
                keyId,
                releaseSequence,
                payloadBase64,
                signatureBase64,
                minimumExclusiveSequence ?: 0L,
                minimumExclusiveSequence != null,
                keys.toString(),
            )
        )
        return VerifiedReferenceManifestSignature(
            result.getString("key_id"),
            result.getLong("release_sequence"),
            Base64.decode(result.getString("payload_base64"), Base64.DEFAULT),
        )
    }

    fun parseReleaseRoot(
        releaseSequence: Long,
        payload: ByteArray,
        contractMajor: Int,
    ): VerifiedReferenceRelease {
        val result = JSONObject(
            nativeParseReleaseRoot(releaseSequence, payload, contractMajor.toLong())
        )
        if (result.getString("status") == "retired") {
            throw ReferenceContractRetiredException(
                releaseSequence,
                result.getString("root_hash"),
                result.getInt("current_contract_major"),
                result.getInt("minimum_supported_contract_major"),
            )
        }
        fun artifact(value: JSONObject): ReferenceReleaseArtifact {
            val patch = value.getString("kind") == "chunk_patch"
            return ReferenceReleaseArtifact(
                contractMajor = value.getInt("contract_major"),
                key = value.getString("key"),
                sha256 = value.getString("sha256"),
                sizeBytes = value.getLong("size_bytes"),
                kind = if (patch) {
                    ReferenceArtifactKind.CHUNK_PATCH
                } else {
                    ReferenceArtifactKind.FULL_GZIP
                },
                fromSha256 = if (patch) value.getString("from_sha256") else null,
                fromSizeBytes = if (patch) value.getLong("from_size_bytes") else null,
            )
        }
        val patches = result.getJSONArray("patches")
        return VerifiedReferenceRelease(
            releaseSequence = result.getLong("release_sequence"),
            rootHash = result.getString("root_hash"),
            datasetId = result.getString("dataset_id"),
            contractMajor = result.getInt("contract_major"),
            targetSha256 = result.getString("target_sha256"),
            targetSizeBytes = result.getLong("target_size_bytes"),
            full = artifact(result.getJSONObject("full")),
            patches = (0 until patches.length()).map { artifact(patches.getJSONObject(it)) },
        )
    }

    fun verifyDatabase(file: File, version: ReferenceVersion) {
        nativeVerifyDatabase(
            file.absolutePath,
            version.contractMajor.toLong(),
            version.datasetId,
        )
    }

    fun rebuildArtifact(
        current: InstalledReferenceVersion?,
        target: ReferenceVersion,
        artifact: ReferenceReleaseArtifact,
        downloaded: File,
        output: File,
        observer: NativeReferenceArtifactObserver,
    ) {
        output.parentFile?.let { parent ->
            check(parent.exists() || parent.mkdirs()) { "cannot create reference rebuild directory" }
        }
        nativeRebuildArtifact(
            current?.file?.absolutePath,
            downloaded.absolutePath,
            output.absolutePath,
            target.sizeBytes,
            target.sha256,
            artifact.kind == ReferenceArtifactKind.CHUNK_PATCH,
            observer,
        )
    }

    private external fun nativeVerifyDatabase(
        databasePath: String,
        contractMajor: Long,
        datasetId: String,
    )

    private external fun nativeRebuildArtifact(
        sourcePath: String?,
        artifactPath: String,
        outputPath: String,
        targetSizeBytes: Long,
        targetSha256: String,
        chunkPatch: Boolean,
        observer: NativeReferenceArtifactObserver,
    )

    private external fun nativeVerifyManifest(
        envelopeVersion: Long,
        algorithm: String,
        keyId: String,
        releaseSequence: Long,
        payloadBase64: String,
        signatureBase64: String,
        minimumExclusiveSequence: Long,
        hasMinimumExclusiveSequence: Boolean,
        trustedKeysJson: String,
    ): String

    private external fun nativeParseReleaseRoot(
        releaseSequence: Long,
        payload: ByteArray,
        contractMajor: Long,
    ): String

    init {
        System.loadLibrary("medicine_core")
    }
}
