package com.medicine.android

import java.io.File


enum class ReferenceArtifactKind {
    FULL_GZIP,
    CHUNK_PATCH,
}

data class ReferenceReleaseArtifact(
    val contractMajor: Int,
    val key: String,
    val sha256: String,
    val sizeBytes: Long,
    val kind: ReferenceArtifactKind,
    val fromSha256: String? = null,
    val fromSizeBytes: Long? = null,
) {
    init {
        require(contractMajor > 0) { "invalid reference artifact contract major" }
        require(SHA256.matches(sha256)) { "invalid reference artifact SHA-256" }
        require(sizeBytes > 0) { "invalid reference artifact size" }
        when (kind) {
            ReferenceArtifactKind.FULL_GZIP -> {
                require(fromSha256 == null && fromSizeBytes == null) {
                    "full reference artifact cannot have patch source"
                }
                require(FULL_KEY.matches(key)) { "invalid reference full artifact key" }
                require(key == "reference/v2/contracts/$contractMajor/full/${targetShaFromKey(key)}.sqlite.gz") {
                    "reference full artifact escaped contract namespace"
                }
            }
            ReferenceArtifactKind.CHUNK_PATCH -> {
                require(fromSha256 != null && SHA256.matches(fromSha256)) {
                    "invalid reference patch source SHA-256"
                }
                require(fromSizeBytes != null && fromSizeBytes > 0) {
                    "invalid reference patch source size"
                }
                require(PATCH_KEY.matches(key)) { "invalid reference patch artifact key" }
                require(key == "reference/v2/contracts/$contractMajor/patch/$fromSha256-${targetShaFromKey(key)}.mpatch") {
                    "reference patch escaped contract namespace"
                }
            }
        }
    }

    companion object {
        private val SHA256 = Regex("[0-9a-f]{64}")
        private val FULL_KEY = Regex("reference/v2/contracts/[1-9][0-9]*/full/[0-9a-f]{64}\\.sqlite\\.gz")
        private val PATCH_KEY = Regex("reference/v2/contracts/[1-9][0-9]*/patch/[0-9a-f]{64}-[0-9a-f]{64}\\.mpatch")

        private fun targetShaFromKey(key: String): String = when {
            key.endsWith(".sqlite.gz") -> key.substringAfterLast('/').removeSuffix(".sqlite.gz")
            key.endsWith(".mpatch") -> key.substringAfterLast('-').removeSuffix(".mpatch")
            else -> ""
        }
    }
}

data class VerifiedReferenceRelease(
    val releaseSequence: Long,
    val rootHash: String,
    val datasetId: String,
    val contractMajor: Int,
    val targetSha256: String,
    val targetSizeBytes: Long,
    val full: ReferenceReleaseArtifact,
    val patches: List<ReferenceReleaseArtifact>,
) {
    init {
        require(releaseSequence > 0) { "invalid verified reference release sequence" }
        require(ROOT_HASH.matches(rootHash)) { "invalid verified reference root hash" }
        ReferenceVersion(datasetId, targetSha256, targetSizeBytes, contractMajor, releaseSequence)
        require(full.kind == ReferenceArtifactKind.FULL_GZIP) {
            "reference release full artifact has wrong kind"
        }
        require(full.contractMajor == contractMajor) {
            "reference full artifact contract does not match release"
        }
        require(full.key == "reference/v2/contracts/$contractMajor/full/$targetSha256.sqlite.gz") {
            "reference full artifact key does not match signed target"
        }
        require(patches.all { it.kind == ReferenceArtifactKind.CHUNK_PATCH }) {
            "reference release patch has wrong kind"
        }
        require(patches.mapNotNull { it.fromSha256 }.size == patches.mapNotNull { it.fromSha256 }.toSet().size) {
            "reference release contains duplicate patch sources"
        }
        patches.forEach { patch ->
            require(patch.contractMajor == contractMajor) {
                "reference patch contract does not match release"
            }
            require(patch.key == "reference/v2/contracts/$contractMajor/patch/${patch.fromSha256}-$targetSha256.mpatch") {
                "reference patch artifact key does not match signed source/target"
            }
            require(patch.sizeBytes < full.sizeBytes) {
                "reference patch must be smaller than the signed full snapshot"
            }
        }
    }

    companion object {
        private val ROOT_HASH = Regex("[0-9a-f]{64}")
    }
}

class ReferenceContractRetiredException(
    val releaseSequence: Long,
    val rootHash: String,
    val currentContractMajor: Int,
    val minimumSupportedContractMajor: Int,
) : IllegalStateException("reference contract is no longer supported")

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
        current: InstalledReferenceVersion?,
        target: ReferenceVersion,
        artifact: ReferenceReleaseArtifact,
        downloaded: File,
        output: File,
        observer: ReferenceUpdateObserver,
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
    UPDATE_REQUIRED,
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
    private val planner: ReferenceLifecyclePlanner = RustReferenceLifecyclePlanner,
) {
    fun checkForUpdate(current: InstalledReferenceVersion): ReferenceUpdateResult =
        ReferenceOperationCoordinator.exclusive {
            checkForUpdateExclusive(current)
        }

    private fun checkForUpdateExclusive(current: InstalledReferenceVersion): ReferenceUpdateResult {
        var releaseSequence: Long? = null
        return try {
            observer.phase("manifest")
            val release = try {
                source.fetchLatest()
            } catch (retired: ReferenceContractRetiredException) {
                releaseSequence = retired.releaseSequence
                return retirementResult(current, retired)
            }
            releaseSequence = release.releaseSequence
            store.observeSignedRoot(release.releaseSequence, release.rootHash)
            when (
                val plan = planner.planUpdate(
                    current.version,
                    store.snapshot().highestActivatedSequence,
                    release,
                )
            ) {
                ReferenceUpdatePlan.UpToDate -> {
                    cleanupUpdateFiles()
                    ReferenceUpdateResult(ReferenceUpdateStatus.UP_TO_DATE, release.releaseSequence)
                }
                ReferenceUpdatePlan.RollbackRejected -> {
                    cleanupUpdateFiles()
                    ReferenceUpdateResult(
                        ReferenceUpdateStatus.ROLLBACK_REJECTED,
                        release.releaseSequence,
                        "signed release sequence is below the activation high-water mark",
                    )
                }
                ReferenceUpdatePlan.IdentityConflict -> {
                    cleanupUpdateFiles()
                    ReferenceUpdateResult(
                        ReferenceUpdateStatus.FAILED,
                        release.releaseSequence,
                        "activated release sequence has a different signed target identity",
                    )
                }
                is ReferenceUpdatePlan.Stage -> {
                    val prepared = preparePreferredArtifact(current, release.releaseSequence, plan)
                    stagePrepared(plan.target, prepared)
                    observer.phase("staged")
                    ReferenceUpdateResult(ReferenceUpdateStatus.STAGED, release.releaseSequence)
                }
            }
        } catch (error: Exception) {
            observer.phase("failed")
            failureResult(error, releaseSequence)
        }
    }

    private fun retirementResult(
        current: InstalledReferenceVersion,
        retired: ReferenceContractRetiredException,
    ): ReferenceUpdateResult {
        store.markContractRetired(
            current.version.contractMajor,
            retired.releaseSequence,
            retired.rootHash,
        )
        cleanupUpdateFiles()
        observer.phase("update-required")
        return ReferenceUpdateResult(
            ReferenceUpdateStatus.UPDATE_REQUIRED,
            retired.releaseSequence,
            "reference contract ${current.version.contractMajor} is retired",
        )
    }

    private fun preparePreferredArtifact(
        current: InstalledReferenceVersion,
        releaseSequence: Long,
        plan: ReferenceUpdatePlan.Stage,
    ): PreparedArtifactFiles {
        val fallback = plan.fallbackFull
        if (fallback == null) {
            return prepareArtifact(
                current,
                plan.target,
                releaseSequence,
                plan.primary,
                preserveCheckpointOnFailure = true,
            )
        }
        return try {
            prepareArtifact(
                current,
                plan.target,
                releaseSequence,
                plan.primary,
                preserveCheckpointOnFailure = false,
            )
        } catch (patchError: Exception) {
            // A signed patch is only an optimization. Any failure before state
            // mutation falls back to the mandatory signed full.
            observer.phase("patch-fallback-full")
            try {
                prepareArtifact(
                    current,
                    plan.target,
                    releaseSequence,
                    fallback,
                    preserveCheckpointOnFailure = true,
                )
            } catch (fullError: Exception) {
                throw IllegalStateException(
                    "reference full fallback failed after patch failure: " +
                        (fullError.message ?: fullError.javaClass.simpleName),
                    fullError,
                ).also { it.addSuppressed(patchError) }
            }
        }
    }

    private fun stagePrepared(targetVersion: ReferenceVersion, prepared: PreparedArtifactFiles) {
        observer.phase("verify-and-stage")
        try {
            store.stagePending(targetVersion, prepared.candidate)
        } finally {
            if (prepared.candidate.exists()) {
                check(prepared.candidate.delete()) { "cannot remove reference update candidate" }
            }
        }
        if (prepared.downloaded.exists()) {
            check(prepared.downloaded.delete()) { "cannot remove staged reference artifact" }
        }
        cleanupUpdateFiles()
    }

    private fun failureResult(error: Exception, releaseSequence: Long?) = ReferenceUpdateResult(
        status = if (error.message?.contains("rollback") == true) {
            ReferenceUpdateStatus.ROLLBACK_REJECTED
        } else {
            ReferenceUpdateStatus.FAILED
        },
        releaseSequence = releaseSequence,
        detail = error.message ?: error.javaClass.simpleName,
    )

    private data class PreparedArtifactFiles(
        val downloaded: File,
        val candidate: File,
    )

    private fun prepareArtifact(
        current: InstalledReferenceVersion,
        target: ReferenceVersion,
        releaseSequence: Long,
        artifact: ReferenceReleaseArtifact,
        preserveCheckpointOnFailure: Boolean,
    ): PreparedArtifactFiles {
        val downloaded = File(
            referenceDir,
            ".artifact-$releaseSequence-${artifact.sha256}.part",
        )
        cleanupUpdateFiles(keepArtifact = downloaded)
        val candidate = File(
            referenceDir,
            ".candidate-$releaseSequence-${target.sha256}.sqlite",
        )
        if (candidate.exists()) {
            check(candidate.delete()) { "cannot remove stale reference update candidate" }
        }
        try {
            observer.phase(
                if (artifact.kind == ReferenceArtifactKind.CHUNK_PATCH) {
                    "patch-download"
                } else {
                    "full-download"
                },
            )
            source.download(artifact, downloaded) { completed, total ->
                observer.progress("download", completed, total)
            }
            observer.phase("rebuild")
            rebuilder.rebuild(
                current,
                target,
                artifact,
                downloaded,
                candidate,
                observer,
            )
            return PreparedArtifactFiles(downloaded, candidate)
        } catch (error: Exception) {
            if (candidate.exists()) {
                check(candidate.delete()) { "cannot remove failed reference update candidate" }
            }
            if (!preserveCheckpointOnFailure && downloaded.exists()) {
                check(downloaded.delete()) { "cannot discard failed reference patch checkpoint" }
            }
            throw error
        }
    }

    private fun cleanupUpdateFiles(keepArtifact: File? = null) {
        val keep = keepArtifact?.canonicalFile
        referenceDir.listFiles()?.forEach { file ->
            if (!file.isFile) return@forEach
            val artifact = file.name.startsWith(".artifact-") && file.name.endsWith(".part")
            val candidate = file.name.startsWith(".candidate-") && file.name.endsWith(".sqlite")
            if (candidate || (artifact && file.canonicalFile != keep)) {
                check(file.delete()) { "cannot remove stale reference update file ${file.name}" }
            }
        }
    }
}
