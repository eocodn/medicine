package com.medicine.android

import java.io.File


enum class ReferenceArtifactKind {
    FULL_GZIP,
    CHUNK_PATCH,
}

data class ReferenceReleaseArtifact(
    val key: String,
    val sha256: String,
    val sizeBytes: Long,
    val kind: ReferenceArtifactKind,
    val fromSha256: String? = null,
    val fromSizeBytes: Long? = null,
) {
    init {
        require(SHA256.matches(sha256)) { "invalid reference artifact SHA-256" }
        require(sizeBytes > 0) { "invalid reference artifact size" }
        when (kind) {
            ReferenceArtifactKind.FULL_GZIP -> {
                require(FULL_KEY.matches(key)) { "invalid reference full artifact key" }
                require(fromSha256 == null && fromSizeBytes == null) { "full reference artifact cannot have patch source" }
            }
            ReferenceArtifactKind.CHUNK_PATCH -> {
                require(PATCH_KEY.matches(key)) { "invalid reference patch artifact key" }
                require(fromSha256 != null && SHA256.matches(fromSha256)) { "invalid reference patch source SHA-256" }
                require(fromSizeBytes != null && fromSizeBytes > 0) { "invalid reference patch source size" }
            }
        }
    }

    companion object {
        private val SHA256 = Regex("[0-9a-f]{64}")
        private val FULL_KEY = Regex("reference/v1/full/[0-9a-f]{64}\\.sqlite\\.gz")
        private val PATCH_KEY = Regex("reference/v1/patch/[0-9a-f]{64}-[0-9a-f]{64}\\.mpatch")
    }
}

data class VerifiedReferenceRelease(
    val releaseSequence: Long,
    val datasetId: String,
    val schemaVersion: String,
    val targetSha256: String,
    val targetSizeBytes: Long,
    val full: ReferenceReleaseArtifact,
    val patches: List<ReferenceReleaseArtifact>,
) {
    init {
        require(releaseSequence > 0) { "invalid verified reference release sequence" }
        // Reuse ReferenceVersion's identity validation so store and network contract cannot drift.
        ReferenceVersion(datasetId, targetSha256, targetSizeBytes, schemaVersion, releaseSequence)
        require(full.kind == ReferenceArtifactKind.FULL_GZIP) { "reference release full artifact has wrong kind" }
        require(full.key == "reference/v1/full/$targetSha256.sqlite.gz") {
            "reference full artifact key does not match signed target"
        }
        require(patches.all { it.kind == ReferenceArtifactKind.CHUNK_PATCH }) { "reference release patch has wrong kind" }
        require(patches.mapNotNull { it.fromSha256 }.size == patches.mapNotNull { it.fromSha256 }.toSet().size) {
            "reference release contains duplicate patch sources"
        }
        patches.forEach { patch ->
            require(patch.key == "reference/v1/patch/${patch.fromSha256}-$targetSha256.mpatch") {
                "reference patch artifact key does not match signed source/target"
            }
            require(patch.sizeBytes < full.sizeBytes) {
                "reference patch must be smaller than the signed full snapshot"
            }
        }
    }
}

interface ReferenceReleaseSource {
    fun fetchLatest(): VerifiedReferenceRelease

    /**
     * Download into [target]. Implementations must treat the file as a resumable checkpoint,
     * verify the final artifact size/SHA-256, and report byte progress while transferring.
     */
    fun download(
        artifact: ReferenceReleaseArtifact,
        target: File,
        progress: (completedBytes: Long, totalBytes: Long) -> Unit,
    )
}

interface ReferenceArtifactRebuilder {
    fun rebuild(
        current: InstalledReferenceVersion,
        artifact: ReferenceReleaseArtifact,
        downloaded: File,
        output: File,
    )
}

interface ReferenceUpdateObserver {
    fun phase(name: String)
    fun progress(name: String, completedBytes: Long, totalBytes: Long)
}

private object NoOpReferenceUpdateObserver : ReferenceUpdateObserver {
    override fun phase(name: String) = Unit
    override fun progress(name: String, completedBytes: Long, totalBytes: Long) = Unit
}

enum class ReferenceUpdateStatus {
    STAGED,
    UP_TO_DATE,
    ROLLBACK_REJECTED,
    FAILED,
}

data class ReferenceUpdateResult(
    val status: ReferenceUpdateStatus,
    val releaseSequence: Long? = null,
    val detail: String? = null,
)

class ReferenceUpdater(
    private val referenceDir: File,
    private val store: ReferenceStore,
    private val source: ReferenceReleaseSource,
    private val rebuilder: ReferenceArtifactRebuilder,
    private val observer: ReferenceUpdateObserver = NoOpReferenceUpdateObserver,
) {
    @Synchronized
    fun checkForUpdate(current: InstalledReferenceVersion): ReferenceUpdateResult {
        var releaseSequence: Long? = null
        var candidate: File? = null
        var downloaded: File? = null
        return try {
            observer.phase("manifest")
            val release = source.fetchLatest()
            releaseSequence = release.releaseSequence
            val state = store.snapshot()
            if (release.releaseSequence < state.highestActivatedSequence) {
                return ReferenceUpdateResult(
                    ReferenceUpdateStatus.ROLLBACK_REJECTED,
                    release.releaseSequence,
                    "signed release sequence is below the activation high-water mark",
                )
            }
            if (release.releaseSequence == state.highestActivatedSequence &&
                current.version.releaseSequence == state.highestActivatedSequence
            ) {
                if (current.version.sha256 == release.targetSha256 &&
                    current.version.sizeBytes == release.targetSizeBytes &&
                    current.version.datasetId == release.datasetId
                ) {
                    return ReferenceUpdateResult(ReferenceUpdateStatus.UP_TO_DATE, release.releaseSequence)
                }
                return ReferenceUpdateResult(
                    ReferenceUpdateStatus.FAILED,
                    release.releaseSequence,
                    "activated release sequence has a different signed target identity",
                )
            }

            val matchingPatches = release.patches.filter {
                it.fromSha256 == current.version.sha256 && it.fromSizeBytes == current.version.sizeBytes
            }
            require(matchingPatches.size <= 1) { "multiple direct patches match the active reference" }
            val artifact = matchingPatches.singleOrNull() ?: release.full
            observer.phase(if (artifact.kind == ReferenceArtifactKind.CHUNK_PATCH) "patch-download" else "full-download")

            downloaded = File(
                referenceDir,
                ".artifact-${release.releaseSequence}-${artifact.sha256}.part",
            )
            source.download(artifact, downloaded) { completed, total ->
                observer.progress("download", completed, total)
            }

            observer.phase("rebuild")
            candidate = File(
                referenceDir,
                ".candidate-${release.releaseSequence}-${release.targetSha256}.sqlite",
            )
            candidate.delete()
            rebuilder.rebuild(current, artifact, downloaded, candidate)
            val targetVersion = ReferenceVersion(
                datasetId = release.datasetId,
                sha256 = release.targetSha256,
                sizeBytes = release.targetSizeBytes,
                schemaVersion = release.schemaVersion,
                releaseSequence = release.releaseSequence,
            )
            observer.phase("verify-and-stage")
            store.stagePending(targetVersion, candidate)
            downloaded.delete()
            observer.phase("staged")
            ReferenceUpdateResult(ReferenceUpdateStatus.STAGED, release.releaseSequence)
        } catch (error: Exception) {
            candidate?.delete()
            observer.phase("failed")
            ReferenceUpdateResult(
                status = ReferenceUpdateStatus.FAILED,
                releaseSequence = releaseSequence,
                detail = error.message ?: error.javaClass.simpleName,
            )
        }
    }
}
