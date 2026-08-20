package com.medicine.android

import android.content.Context
import android.os.StatFs
import java.io.File
import java.security.MessageDigest

data class InstalledReference(
    val database: File,
    val datasetId: String,
    val version: ReferenceVersion,
    val store: ReferenceStore,
    val referenceDir: File,
    val recoveryReason: String? = null,
)

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
) {
    fun ensureInstalled(expectedSchemaVersion: String): InstalledReferenceVersion =
        ReferenceOperationCoordinator.exclusive {
            ensureInstalledExclusive(expectedSchemaVersion)
        }

    private fun ensureInstalledExclusive(expectedSchemaVersion: String): InstalledReferenceVersion {
        store.openForStartup(expectedSchemaVersion)?.let {
            cleanupBootstrapFiles()
            observer.phase("ready")
            return it
        }

        observer.phase("manifest")
        val release = source.fetchLatest()
        require(release.schemaVersion == expectedSchemaVersion) {
            "reference release schema is incompatible with this app"
        }
        val state = store.snapshot()
        require(release.releaseSequence >= state.highestActivatedSequence) {
            "reference rollback is not allowed"
        }
        val version = ReferenceVersion(
            datasetId = release.datasetId,
            sha256 = release.targetSha256,
            sizeBytes = release.targetSizeBytes,
            schemaVersion = release.schemaVersion,
            releaseSequence = release.releaseSequence,
        )
        val artifact = release.full
        val downloaded = File(
            referenceDir,
            ".bootstrap-artifact-${release.releaseSequence}-${artifact.sha256}.part",
        )
        cleanupBootstrapFiles(keepArtifact = downloaded)
        store.cleanupForBootstrap(version)

        // A process may die after the content-addressed DB rename but before the
        // AtomicFile state commit. Adopt that fully verified target instead of
        // downloading it again; never adopt a partial or unverifiable file.
        if (store.fileFor(version).isFile) {
            runCatching { store.installInitial(version, candidate = null) }
                .getOrNull()
                ?.let {
                    observer.phase("ready")
                    return it
                }
        }

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
            rebuilder.rebuild(null, artifact, downloaded, candidate)
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

class AndroidReferenceInstaller(
    private val context: Context,
    private val observer: ReferenceUpdateObserver = NoOpReferenceBootstrapObserver,
) {
    fun install(): InstalledReference {
        val baseUrl = BuildConfig.REFERENCE_UPDATE_BASE_URL.trim()
        require(baseUrl.isNotEmpty()) { "reference distribution base URL is not configured" }
        val referenceDir = File(context.filesDir, "reference").apply {
            check(exists() || mkdirs()) { "cannot create reference data directory" }
        }
        val store = ReferenceStore(
            referenceDir,
            AtomicFileReferenceStateStorage(File(referenceDir, STATE_FILE)),
            PythonReferenceDatabaseVerifier(),
            fileSealProvider = AndroidReferenceFileSealProvider(),
        )
        val source = HttpsReferenceReleaseSource(
            baseUrl,
            ReferenceManifestVerifier(ReferenceTrust.trustedPublicKeys),
        )
        val selected = ReferenceBootstrapper(
            referenceDir,
            store,
            source,
            PythonReferenceArtifactRebuilder(),
            AndroidReferenceStorageCapacity(),
            observer,
        ).ensureInstalled(ReferenceRuntimePolicy.SCHEMA_VERSION)
        return InstalledReference(
            database = selected.file,
            datasetId = selected.version.datasetId,
            version = selected.version,
            store = store,
            referenceDir = referenceDir,
            recoveryReason = selected.recoveryReason,
        )
    }

    companion object {
        private const val STATE_FILE = "state.v1"
    }
}