package com.medicine.android

import android.content.Context
import android.os.StatFs
import java.io.File
import java.security.MessageDigest

internal const val REFERENCE_STATE_FILE = "state.v1"

data class InstalledReference(
    val database: File?,
    val datasetId: String?,
    val version: ReferenceVersion?,
    val store: ReferenceStore,
    val referenceDir: File,
    val recoveryReason: String? = null,
    val unavailableReason: String? = null,
) {
    val referenceAvailable: Boolean
        get() = database != null && version != null && !store.isContractRetired(version.contractMajor)
    val referenceUnavailableReason: String?
        get() = if (referenceAvailable) null else (unavailableReason ?: "update_required")
}

interface ReferenceStorageCapacity {
    fun availableBytes(path: File): Long
}

class AndroidReferenceStorageCapacity : ReferenceStorageCapacity {
    override fun availableBytes(path: File): Long = StatFs(path.absolutePath).availableBytes
}

class ReferenceBootstrapStorageException(
    val requiredBytes: Long,
    val availableBytes: Long,
) : IllegalStateException("insufficient storage for reference bootstrap")

sealed interface ReferenceBootstrapPreparation {
    data class Ready(val installed: InstalledReferenceVersion) : ReferenceBootstrapPreparation

    class Download internal constructor(
        internal val release: VerifiedReferenceRelease,
        internal val version: ReferenceVersion,
        internal val artifact: ReferenceReleaseArtifact,
        internal val downloaded: File,
        internal val checkpointBytes: Long,
    ) : ReferenceBootstrapPreparation {
        val downloadSizeBytes: Long
            get() = artifact.sizeBytes - checkpointBytes

        val totalDownloadBytes: Long
            get() = artifact.sizeBytes
    }
}

private object NoOpReferenceBootstrapObserver : ReferenceUpdateObserver {
    override fun phase(name: String) = Unit
    override fun progress(name: String, completedBytes: Long, totalBytes: Long) = Unit
}

class ReferenceBootstrapper(
    private val referenceDir: File,
    private val store: ReferenceStore,
    private val source: ReferenceReleaseSource,
    private val rebuilder: ReferenceArtifactRebuilder,
    private val storageCapacity: ReferenceStorageCapacity,
    private val observer: ReferenceUpdateObserver = NoOpReferenceBootstrapObserver,
    private val planner: ReferenceLifecyclePlanner = RustReferenceLifecyclePlanner,
) {
    fun ensureInstalled(expectedContractMajor: Int): InstalledReferenceVersion =
        ReferenceOperationCoordinator.exclusive {
            installPreparedExclusive(prepareExclusive(expectedContractMajor), expectedContractMajor)
        }

    fun prepare(expectedContractMajor: Int): ReferenceBootstrapPreparation =
        ReferenceOperationCoordinator.exclusive {
            prepareExclusive(expectedContractMajor)
        }

    fun installPrepared(
        preparation: ReferenceBootstrapPreparation,
        expectedContractMajor: Int,
    ): InstalledReferenceVersion = ReferenceOperationCoordinator.exclusive {
        installPreparedExclusive(preparation, expectedContractMajor)
    }

    fun ensureInstalledOrRetired(expectedContractMajor: Int): InstalledReferenceVersion? =
        ReferenceOperationCoordinator.exclusive {
            store.openForStartup(expectedContractMajor)?.let { return@exclusive it }
            if (store.isContractRetired(expectedContractMajor)) return@exclusive null
            try {
                installPreparedExclusive(prepareExclusive(expectedContractMajor), expectedContractMajor)
            } catch (retired: ReferenceContractRetiredException) {
                store.markContractRetired(
                    expectedContractMajor,
                    retired.releaseSequence,
                    retired.rootHash,
                )
                null
            }
        }

    private fun prepareExclusive(expectedContractMajor: Int): ReferenceBootstrapPreparation {
        val startup = prepareStage("prepare_open_startup") {
            store.openForStartup(expectedContractMajor)
        }
        startup?.let {
            cleanupBootstrapFiles()
            observer.phase("ready")
            return ReferenceBootstrapPreparation.Ready(it)
        }

        observer.phase("manifest")
        val release = source.fetchLatest()
        prepareStage("prepare_observe_root") {
            store.observeSignedRoot(release.releaseSequence, release.rootHash)
        }
        val state = prepareStage("prepare_snapshot") { store.snapshot() }
        val plan = prepareStage("prepare_plan") {
            planner.planBootstrap(
                expectedContractMajor,
                state.highestActivatedSequence,
                release,
            )
        }
        val version = (plan as ReferenceBootstrapPlan.Download).target
        val artifact = plan.full
        val downloaded = File(
            referenceDir,
            ".bootstrap-artifact-${release.releaseSequence}-${artifact.sha256}.part",
        )
        prepareStage("prepare_cleanup") {
            cleanupBootstrapFiles(keepArtifact = downloaded)
        }

        // A process may die after the content-addressed DB rename but before the
        // AtomicFile state commit. Adopt that fully verified target instead of
        // downloading it again; never adopt a partial or unverifiable file.
        if (store.fileFor(version).isFile) {
            runCatching { store.installInitial(version, candidate = null) }
                .getOrNull()
                ?.let {
                    observer.phase("ready")
                    return ReferenceBootstrapPreparation.Ready(it)
                }
        }

        val checkpointBytes = prepareStage("prepare_checkpoint") {
            usableCheckpointBytes(downloaded, artifact)
        }
        return ReferenceBootstrapPreparation.Download(
            release = release,
            version = version,
            artifact = artifact,
            downloaded = downloaded,
            checkpointBytes = checkpointBytes,
        )
    }

    private fun <T> prepareStage(stage: String, block: () -> T): T = try {
        block()
    } catch (error: ReferenceBootstrapPrepareStageException) {
        throw error
    } catch (error: Throwable) {
        throw ReferenceBootstrapPrepareStageException(stage, error)
    }

    private fun installPreparedExclusive(
        preparation: ReferenceBootstrapPreparation,
        expectedContractMajor: Int,
    ): InstalledReferenceVersion {
        store.openForStartup(expectedContractMajor)?.let {
            cleanupBootstrapFiles()
            observer.phase("ready")
            return it
        }
        if (preparation is ReferenceBootstrapPreparation.Ready) {
            return preparation.installed
        }
        preparation as ReferenceBootstrapPreparation.Download
        val release = preparation.release
        val version = preparation.version
        val artifact = preparation.artifact
        val downloaded = preparation.downloaded
        store.cleanupForBootstrap(version)

        val candidate = File(
            referenceDir,
            ".bootstrap-candidate-${release.releaseSequence}-${release.targetSha256}.sqlite",
        )
        candidate.delete()

        val checkpointBytes = usableCheckpointBytes(downloaded, artifact)
        val additionalBytes = Math.addExact(
            artifact.sizeBytes - checkpointBytes,
            Math.addExact(release.targetSizeBytes, STORAGE_SAFETY_MARGIN_BYTES),
        )
        val availableBytes = storageCapacity.availableBytes(referenceDir)
        if (availableBytes < additionalBytes) {
            throw ReferenceBootstrapStorageException(additionalBytes, availableBytes)
        }

        try {
            observer.phase("full-download")
            source.download(artifact, downloaded) { completed, total ->
                observer.progress("download", completed, total)
            }
            observer.phase("rebuild")
            rebuilder.rebuild(null, version, artifact, downloaded, candidate, observer)
            observer.phase("verify-and-install")
            val installed = store.installInitial(version, candidate)
            downloaded.delete()
            cleanupBootstrapFiles()
            observer.phase("ready")
            return installed
        } finally {
            // Download checkpoints intentionally survive interruption for Range
            // resume. Rebuild candidates are disposable and must never be adopted.
            candidate.delete()
        }
    }

    companion object {
        private const val STORAGE_SAFETY_MARGIN_BYTES = 16L * 1024L * 1024L
    }

    private fun usableCheckpointBytes(
        checkpoint: File,
        artifact: ReferenceReleaseArtifact,
    ): Long {
        if (!checkpoint.isFile) return 0L
        val size = checkpoint.length()
        if (size < artifact.sizeBytes) return size
        if (size == artifact.sizeBytes && sha256(checkpoint) == artifact.sha256) return size
        check(checkpoint.delete()) { "cannot discard invalid reference bootstrap checkpoint" }
        return 0L
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                if (read > 0) digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun cleanupBootstrapFiles(keepArtifact: File? = null) {
        val keep = keepArtifact?.canonicalFile
        referenceDir.listFiles()?.forEach { file ->
            if (!file.isFile) return@forEach
            val artifact = file.name.startsWith(".bootstrap-artifact-") && file.name.endsWith(".part")
            val candidate = file.name.startsWith(".bootstrap-candidate-") && file.name.endsWith(".sqlite")
            val shouldDelete = candidate || (artifact && file.canonicalFile != keep)
            if (shouldDelete) {
                check(file.delete()) { "cannot remove stale reference bootstrap file ${file.name}" }
            }
        }
    }
}

class ReferenceBootstrapPrepareStageException(
    val stage: String,
    cause: Throwable,
) : IllegalStateException("reference bootstrap prepare stage failed: $stage: ${cause.message}", cause)

class AndroidReferenceInstaller(
    private val context: Context,
    private val observer: ReferenceUpdateObserver = NoOpReferenceBootstrapObserver,
) {
    private val referenceDir = File(context.filesDir, "reference").apply {
        check(exists() || mkdirs()) { "cannot create reference data directory" }
    }
    private val store = ReferenceStore(
            referenceDir,
            AtomicFileReferenceStateStorage(File(referenceDir, REFERENCE_STATE_FILE)),
            RustReferenceDatabaseVerifier(),
            fileSealProvider = AndroidReferenceFileSealProvider(),
        )
    private val source = HttpsReferenceReleaseSource(
        BuildConfig.REFERENCE_UPDATE_BASE_URL.trim().also {
            require(it.isNotEmpty()) { "reference distribution base URL is not configured" }
        },
        ReferenceManifestVerifier(ReferenceTrust.trustedPublicKeys),
    )
    private val bootstrapper = ReferenceBootstrapper(
        referenceDir,
        store,
        source,
        RustReferenceArtifactRebuilder(),
        AndroidReferenceStorageCapacity(),
        observer,
    )

    fun prepare(): ReferenceBootstrapPreparation? {
        if (store.isContractRetired(ReferenceRuntimePolicy.CONTRACT_MAJOR)) return null
        return try {
            bootstrapper.prepare(ReferenceRuntimePolicy.CONTRACT_MAJOR)
        } catch (retired: ReferenceContractRetiredException) {
            store.markContractRetired(
                ReferenceRuntimePolicy.CONTRACT_MAJOR,
                retired.releaseSequence,
                retired.rootHash,
            )
            null
        }
    }

    fun installPrepared(preparation: ReferenceBootstrapPreparation?): InstalledReference {
        val selected = preparation?.let {
            bootstrapper.installPrepared(it, ReferenceRuntimePolicy.CONTRACT_MAJOR)
        }
        if (selected == null) {
            return InstalledReference(
                database = null,
                datasetId = null,
                version = null,
                store = store,
                referenceDir = referenceDir,
                unavailableReason = "update_required",
            )
        }
        return InstalledReference(
            database = selected.file,
            datasetId = selected.version.datasetId,
            version = selected.version,
            store = store,
            referenceDir = referenceDir,
            recoveryReason = selected.recoveryReason,
        )
    }

    fun install(): InstalledReference = installPrepared(prepare())

}
