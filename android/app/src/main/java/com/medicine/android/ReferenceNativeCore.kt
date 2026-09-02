package com.medicine.android

import android.util.Base64
import org.json.JSONObject
import java.io.File

interface NativeReferenceArtifactObserver {
    fun progress(phase: String, completedBytes: Long, totalBytes: Long)
    fun checkpoint(path: String)
}

object ReferenceNativeCore {
    fun createBootstrapCoordinator(): Long = nativeCreateBootstrapCoordinator()

    fun destroyBootstrapCoordinator(handle: Long) = nativeDestroyBootstrapCoordinator(handle)

    fun bootstrapBeginPrepare(handle: Long): Boolean = nativeBootstrapBeginPrepare(handle)

    fun bootstrapResetForPrepare(handle: Long) = nativeBootstrapResetForPrepare(handle)

    fun bootstrapPreparedDownload(handle: Long, completedBytes: Long, totalBytes: Long) =
        nativeBootstrapPreparedDownload(handle, completedBytes, totalBytes)

    fun bootstrapBeginInstall(handle: Long): Boolean = nativeBootstrapBeginInstall(handle)

    fun bootstrapPhase(handle: Long, phase: String) = nativeBootstrapPhase(handle, phase)

    fun bootstrapProgress(handle: Long, completedBytes: Long, totalBytes: Long) =
        nativeBootstrapProgress(handle, completedBytes, totalBytes)

    fun bootstrapReady(handle: Long) = nativeBootstrapReady(handle)

    fun bootstrapUnavailable(handle: Long, detail: String) = nativeBootstrapUnavailable(handle, detail)

    fun bootstrapFailed(handle: Long, detail: String) = nativeBootstrapFailed(handle, detail)

    fun bootstrapSnapshot(handle: Long): String = nativeBootstrapSnapshot(handle)

    fun planReferenceBootstrap(
        expectedContractMajor: Int,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceBootstrapPlan {
        val result = JSONObject(
            nativePlanReferenceBootstrap(
                expectedContractMajor.toLong(),
                highestActivatedSequence,
                releaseJson(release).toString(),
            )
        )
        check(result.getString("status") == "bootstrap") {
            "native reference bootstrap planner returned an invalid result"
        }
        return ReferenceBootstrapPlan.Download(
            target = release.targetVersion(),
            full = release.full,
        )
    }

    fun planReferenceUpdate(
        current: ReferenceVersion,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceUpdatePlan {
        val result = JSONObject(
            nativePlanReferenceUpdate(
                versionJson(current).toString(),
                highestActivatedSequence,
                releaseJson(release).toString(),
            )
        )
        return when (result.getString("status")) {
            "up_to_date" -> ReferenceUpdatePlan.UpToDate
            "rollback_rejected" -> ReferenceUpdatePlan.RollbackRejected
            "identity_conflict" -> ReferenceUpdatePlan.IdentityConflict
            "stage" -> {
                val primaryKey = result.getString("primary_key")
                val fallbackKey = result.optString("fallback_full_key").takeIf { it.isNotEmpty() }
                val artifacts = listOf(release.full) + release.patches
                val primary = requireNotNull(artifacts.singleOrNull { it.key == primaryKey }) {
                    "native reference update planner selected an unknown artifact"
                }
                val fallback = fallbackKey?.let { key ->
                    requireNotNull(artifacts.singleOrNull { it.key == key }) {
                        "native reference update planner selected an unknown fallback artifact"
                    }
                }
                ReferenceUpdatePlan.Stage(
                    target = release.targetVersion(),
                    primary = primary,
                    fallbackFull = fallback,
                )
            }
            else -> error("native reference update planner returned an invalid result")
        }
    }

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

    fun verifyRuntimeCapabilities(file: File) {
        nativeVerifyRuntimeCapabilities(file.absolutePath)
    }

    fun verifyRuntimeMaterialization(file: File) {
        nativeVerifyRuntimeMaterialization(file.absolutePath)
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

    private external fun nativeVerifyRuntimeCapabilities(databasePath: String)
    private external fun nativeVerifyRuntimeMaterialization(databasePath: String)

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

    private external fun nativePlanReferenceBootstrap(
        expectedContractMajor: Long,
        highestActivatedSequence: Long,
        releaseJson: String,
    ): String

    private external fun nativePlanReferenceUpdate(
        currentJson: String,
        highestActivatedSequence: Long,
        releaseJson: String,
    ): String

    private external fun nativeCreateBootstrapCoordinator(): Long
    private external fun nativeDestroyBootstrapCoordinator(handle: Long)
    private external fun nativeBootstrapBeginPrepare(handle: Long): Boolean
    private external fun nativeBootstrapResetForPrepare(handle: Long)
    private external fun nativeBootstrapPreparedDownload(
        handle: Long,
        completedBytes: Long,
        totalBytes: Long,
    )
    private external fun nativeBootstrapBeginInstall(handle: Long): Boolean
    private external fun nativeBootstrapPhase(handle: Long, phase: String)
    private external fun nativeBootstrapProgress(handle: Long, completedBytes: Long, totalBytes: Long)
    private external fun nativeBootstrapReady(handle: Long)
    private external fun nativeBootstrapUnavailable(handle: Long, detail: String)
    private external fun nativeBootstrapFailed(handle: Long, detail: String)
    private external fun nativeBootstrapSnapshot(handle: Long): String

    init {
        System.loadLibrary("medicine_core")
    }

    private fun versionJson(version: ReferenceVersion): JSONObject = JSONObject()
        .put("datasetId", version.datasetId)
        .put("sha256", version.sha256)
        .put("sizeBytes", version.sizeBytes)
        .put("contractMajor", version.contractMajor)
        .put("releaseSequence", version.releaseSequence)

    private fun releaseJson(release: VerifiedReferenceRelease): JSONObject = JSONObject()
        .put("release_sequence", release.releaseSequence)
        .put("root_hash", release.rootHash)
        .put("dataset_id", release.datasetId)
        .put("contract_major", release.contractMajor)
        .put("target_sha256", release.targetSha256)
        .put("target_size_bytes", release.targetSizeBytes)
        .put("full", artifactJson(release.full))
        .put("patches", release.patches.map(::artifactJson))

    private fun artifactJson(artifact: ReferenceReleaseArtifact): JSONObject = JSONObject()
        .put("contract_major", artifact.contractMajor)
        .put("key", artifact.key)
        .put("sha256", artifact.sha256)
        .put("size_bytes", artifact.sizeBytes)
        .put(
            "kind",
            if (artifact.kind == ReferenceArtifactKind.CHUNK_PATCH) "chunk_patch" else "full_gzip",
        )
        .put("from_sha256", artifact.fromSha256 ?: JSONObject.NULL)
        .put("from_size_bytes", artifact.fromSizeBytes ?: JSONObject.NULL)

    private fun VerifiedReferenceRelease.targetVersion() = ReferenceVersion(
        datasetId = datasetId,
        sha256 = targetSha256,
        sizeBytes = targetSizeBytes,
        contractMajor = contractMajor,
        releaseSequence = releaseSequence,
    )
}
