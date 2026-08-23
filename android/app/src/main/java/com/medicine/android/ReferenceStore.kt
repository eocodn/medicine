package com.medicine.android

import android.util.AtomicFile
import java.io.File
import java.io.FileNotFoundException
import java.io.FileOutputStream
import java.security.MessageDigest


data class ReferenceVersion(
    val datasetId: String,
    val sha256: String,
    val sizeBytes: Long,
    val contractMajor: Int,
    val releaseSequence: Long,
) {
    init {
        require(DATASET_ID.matches(datasetId)) { "invalid reference dataset id" }
        require(SHA256.matches(sha256)) { "invalid reference SHA-256" }
        require(sizeBytes > 0) { "invalid reference size" }
        require(contractMajor > 0) { "invalid reference contract major" }
        require(releaseSequence >= 0) { "invalid reference release sequence" }
    }

    companion object {
        private val DATASET_ID = Regex("sha256:[0-9a-f]{64}")
        private val SHA256 = Regex("[0-9a-f]{64}")
    }
}

data class ReferenceFileSeal(
    val sizeBytes: Long,
    val modifiedMarker: Long,
    val changedMarker: Long,
    val identityKey: String,
    val writable: Boolean,
)

interface ReferenceFileSealProvider {
    fun capture(file: File): ReferenceFileSeal?
}

interface ReferenceContentHasher {
    fun sha256(file: File): String
}

private object Sha256ReferenceContentHasher : ReferenceContentHasher {
    override fun sha256(file: File): String {
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
}

data class ReferenceStoreState(
    val active: ReferenceVersion? = null,
    val previous: ReferenceVersion? = null,
    val pending: ReferenceVersion? = null,
    val highestActivatedSequence: Long = 0,
    val highestSeenRootSequence: Long = 0,
    val highestSeenRootHash: String? = null,
    val highestRetiredContractMajor: Int = 0,
    val activeSeal: ReferenceFileSeal? = null,
    val previousSeal: ReferenceFileSeal? = null,
    val pendingSeal: ReferenceFileSeal? = null,
) {
    init {
        require(highestActivatedSequence >= 0) { "invalid reference activation sequence" }
        require(highestSeenRootSequence >= 0) { "invalid signed root sequence" }
        require(highestRetiredContractMajor >= 0) { "invalid retired reference contract major" }
        require(
            (highestSeenRootSequence == 0L && highestSeenRootHash == null) ||
                (highestSeenRootSequence > 0L && highestSeenRootHash?.matches(Regex("[0-9a-f]{64}")) == true)
        ) { "invalid signed root high-water mark" }
        require(active == null || active.releaseSequence <= highestActivatedSequence) {
            "active reference sequence exceeds activation high-water mark"
        }
        require(previous == null || previous.releaseSequence <= highestActivatedSequence) {
            "previous reference sequence exceeds activation high-water mark"
        }
        require(active != null || activeSeal == null) { "active seal requires active reference" }
        require(previous != null || previousSeal == null) { "previous seal requires previous reference" }
        require(pending != null || pendingSeal == null) { "pending seal requires pending reference" }
    }
}

data class InstalledReferenceVersion(
    val version: ReferenceVersion,
    val file: File,
    val recoveryReason: String? = null,
)

interface ReferenceStateStorage {
    fun read(): ByteArray?
    fun write(value: ByteArray)
}

class AtomicFileReferenceStateStorage(file: File) : ReferenceStateStorage {
    private val base = file
    private val atomic = AtomicFile(file)

    override fun read(): ByteArray? = try {
        atomic.openRead().use { it.readBytes() }
    } catch (_: FileNotFoundException) {
        null
    }

    override fun write(value: ByteArray) {
        base.parentFile?.let { parent ->
            check(parent.exists() || parent.mkdirs()) { "cannot create reference state directory" }
        }
        var output: FileOutputStream? = null
        try {
            output = atomic.startWrite()
            output.write(value)
            output.fd.sync()
            atomic.finishWrite(output)
            output = null
        } finally {
            if (output != null) atomic.failWrite(output)
        }
    }
}

interface ReferenceDatabaseVerifier {
    fun verify(file: File, version: ReferenceVersion)
    fun verifyRuntimeCapabilities(file: File, version: ReferenceVersion)
}

class ReferenceStore(
    private val root: File,
    private val stateStorage: ReferenceStateStorage,
    private val databaseVerifier: ReferenceDatabaseVerifier,
    private val fileSealProvider: ReferenceFileSealProvider? = null,
    private val contentHasher: ReferenceContentHasher = Sha256ReferenceContentHasher,
) {
    init {
        check(root.exists() || root.mkdirs()) { "cannot create reference data directory" }
    }

    fun fileFor(version: ReferenceVersion): File = File(root, "mobile-${version.sha256}.sqlite")

    fun snapshot(): ReferenceStoreState = ReferenceStateCodec.decode(stateStorage.read())

    fun openForStartup(expectedContractMajor: Int): InstalledReferenceVersion? {
        require(expectedContractMajor > 0) { "invalid expected reference contract major" }
        var recoveryReason: String? = null
        val encodedState = stateStorage.read()
        val legacyState = ReferenceStateCodec.isLegacyV1(encodedState)
        var state = ReferenceStateCodec.decode(encodedState)

        val activation = activatePendingIfValid(state, expectedContractMajor)
        state = activation.state
        recoveryReason = recoveryReason ?: activation.recoveryReason

        val activeVerification = state.active?.let {
            verifyForStartup(it, state.activeSeal, expectedContractMajor)
        }
        val previousVerification = if (activeVerification?.valid == true) {
            null
        } else {
            state.previous?.let {
                verifyForStartup(it, state.previousSeal, expectedContractMajor)
            }
        }
        val selected = when {
            activeVerification?.valid == true -> state.active!!
            previousVerification?.valid == true -> {
                recoveryReason = recoveryReason ?: "active reference invalid; using previous LKG"
                state.previous!!
            }
            else -> return null
        }

        val normalized = if (selected == state.active) {
            state.copy(activeSeal = activeVerification?.seal)
        } else {
            state.copy(
                active = selected,
                activeSeal = previousVerification?.seal,
                previous = null,
                previousSeal = null,
            )
        }
        if (normalized != state || legacyState) writeState(normalized)
        cleanupUnreferenced(normalized)
        return InstalledReferenceVersion(selected, fileFor(selected), recoveryReason)
    }

    fun cleanupForBootstrap(version: ReferenceVersion) {
        cleanupUnreferenced(snapshot(), extraKeep = setOf(version))
    }

    fun observeSignedRoot(releaseSequence: Long, rootHash: String) {
        val state = snapshot()
        val observed = withObservedSignedRoot(state, releaseSequence, rootHash)
        if (observed != state) writeState(observed)
    }

    private fun withObservedSignedRoot(
        state: ReferenceStoreState,
        releaseSequence: Long,
        rootHash: String,
    ): ReferenceStoreState {
        require(releaseSequence > 0) { "signed root sequence must be positive" }
        require(rootHash.matches(Regex("[0-9a-f]{64}"))) { "invalid signed root hash" }
        return when {
            releaseSequence < state.highestSeenRootSequence ->
                throw IllegalArgumentException("signed reference root rollback is not allowed")
            releaseSequence == state.highestSeenRootSequence -> {
                require(state.highestSeenRootHash == rootHash) {
                    "signed reference root changed without advancing sequence"
                }
                state
            }
            else ->
                state.copy(
                    highestSeenRootSequence = releaseSequence,
                    highestSeenRootHash = rootHash,
                )
        }
    }

    fun markContractRetired(contractMajor: Int, releaseSequence: Long, rootHash: String) {
        require(contractMajor > 0) { "retired reference contract major must be positive" }
        val state = snapshot()
        val observed = withObservedSignedRoot(state, releaseSequence, rootHash)
        val retired = observed.copy(
            highestRetiredContractMajor = maxOf(
                observed.highestRetiredContractMajor,
                contractMajor,
            ),
        )
        if (retired != state) writeState(retired)
    }

    fun isContractRetired(contractMajor: Int): Boolean {
        require(contractMajor > 0) { "reference contract major must be positive" }
        return snapshot().highestRetiredContractMajor >= contractMajor
    }

    fun installInitial(version: ReferenceVersion, candidate: File?): InstalledReferenceVersion {
        require(version.releaseSequence > 0) { "downloaded reference release sequence must be positive" }
        candidate?.let {
            require(it.parentFile?.canonicalFile == root.canonicalFile) {
                "reference candidate must be staged in the reference directory"
            }
        }

        val state = snapshot()
        require(version.releaseSequence >= state.highestActivatedSequence) {
            "reference rollback is not allowed"
        }
        require(
            version.releaseSequence != state.highestActivatedSequence || state.active == version
        ) {
            "reference release sequence identity conflict"
        }
        val target = fileFor(version)
        if (!isDatabaseVerified(target, version)) {
            if (target.exists()) check(target.delete()) {
                "cannot remove corrupted content-addressed reference target"
            }
            val source = requireNotNull(candidate) {
                "verified initial reference candidate is required"
            }
            check(isDatabaseVerified(source, version)) { "reference candidate verification failed" }
            check(source.renameTo(target)) { "cannot atomically install initial reference candidate" }
        } else {
            candidate?.delete()
        }
        check(target.setReadOnly()) { "cannot make initial reference read-only" }
        check(isDatabaseVerified(target, version)) { "installed initial reference verification failed" }
        val activeSeal = captureVerifiedSeal(target)

        val activated = ReferenceStoreState(
            active = version,
            previous = null,
            pending = null,
            highestActivatedSequence = maxOf(state.highestActivatedSequence, version.releaseSequence),
            highestSeenRootSequence = state.highestSeenRootSequence,
            highestSeenRootHash = state.highestSeenRootHash,
            highestRetiredContractMajor = state.highestRetiredContractMajor,
            activeSeal = activeSeal,
        )
        writeState(activated)
        cleanupUnreferenced(activated)
        return InstalledReferenceVersion(version, target)
    }

    fun stagePending(version: ReferenceVersion, candidate: File) {
        require(version.releaseSequence > 0) { "downloaded reference release sequence must be positive" }
        require(candidate.parentFile?.canonicalFile == root.canonicalFile) {
            "reference candidate must be staged in the reference directory"
        }
        check(isDatabaseVerified(candidate, version)) { "reference candidate verification failed" }

        val state = snapshot()
        require(version.releaseSequence >= state.highestActivatedSequence) {
            "reference rollback is not allowed"
        }
        if (version.releaseSequence == state.highestActivatedSequence) {
            require((state.active?.releaseSequence ?: 0L) < state.highestActivatedSequence) {
                "reference release is already activated"
            }
        }
        state.pending?.let { pending ->
            require(version.releaseSequence >= pending.releaseSequence) {
                "older reference release cannot replace a newer pending release"
            }
        }

        val target = fileFor(version)
        if (target.exists() && !isDatabaseVerified(target, version)) {
            check(target.delete()) { "cannot remove corrupted content-addressed reference target" }
        }
        if (target.exists()) {
            candidate.delete()
        } else {
            check(candidate.renameTo(target)) { "cannot atomically install reference candidate" }
        }
        check(target.setReadOnly()) { "cannot make reference candidate read-only" }
        check(isDatabaseVerified(target, version)) { "installed reference candidate verification failed" }
        val pendingSeal = captureVerifiedSeal(target)

        writeState(state.copy(pending = version, pendingSeal = pendingSeal))
    }

    private data class PendingActivation(
        val state: ReferenceStoreState,
        val recoveryReason: String? = null,
    )

    private fun activatePendingIfValid(
        state: ReferenceStoreState,
        expectedContractMajor: Int,
    ): PendingActivation {
        val pending = state.pending ?: return PendingActivation(state)
        if (pending.releaseSequence < state.highestSeenRootSequence) {
            fileFor(pending).takeIf { pending != state.active && pending != state.previous }?.delete()
            val cleared = state.copy(pending = null, pendingSeal = null)
            writeState(cleared)
            return PendingActivation(
                cleared,
                "pending reference predates a newer signed root; retained current LKG",
            )
        }
        if (pending.contractMajor != expectedContractMajor || !isDatabaseVerified(pending)) {
            fileFor(pending).delete()
            val cleared = state.copy(pending = null, pendingSeal = null)
            writeState(cleared)
            return PendingActivation(
                cleared,
                "pending reference is incompatible or failed re-verification; retained current LKG",
            )
        }
        val canAdvance = pending.releaseSequence > state.highestActivatedSequence
        val canRepair = pending.releaseSequence == state.highestActivatedSequence &&
            (state.active?.releaseSequence ?: 0L) < state.highestActivatedSequence
        if (!canAdvance && !canRepair) {
            fileFor(pending).takeIf { pending != state.active && pending != state.previous }?.delete()
            val cleared = state.copy(pending = null, pendingSeal = null)
            writeState(cleared)
            return PendingActivation(
                cleared,
                "pending reference sequence is no longer eligible; retained current LKG",
            )
        }

        val pendingFile = fileFor(pending)
        check(pendingFile.setReadOnly()) { "cannot keep pending reference read-only" }
        val activatedSeal = captureVerifiedSeal(pendingFile)
        val activeVerification = state.active
            ?.takeIf { it != pending }
            ?.let { verifyForStartup(it, state.activeSeal, expectedContractMajor) }
        val previousVerification = if (activeVerification?.valid == true) {
            null
        } else {
            state.previous
                ?.takeIf { it != pending }
                ?.let { verifyForStartup(it, state.previousSeal, expectedContractMajor) }
        }
        val validCurrent = when {
            activeVerification?.valid == true -> state.active
            previousVerification?.valid == true -> state.previous
            else -> null
        }
        val validCurrentSeal = when (validCurrent) {
            state.active -> activeVerification?.seal
            state.previous -> previousVerification?.seal
            else -> null
        }
        val activated = ReferenceStoreState(
            active = pending,
            previous = validCurrent,
            pending = null,
            highestActivatedSequence = maxOf(state.highestActivatedSequence, pending.releaseSequence),
            highestSeenRootSequence = state.highestSeenRootSequence,
            highestSeenRootHash = state.highestSeenRootHash,
            highestRetiredContractMajor = state.highestRetiredContractMajor,
            activeSeal = activatedSeal,
            previousSeal = validCurrentSeal,
            pendingSeal = null,
        )
        writeState(activated)
        return PendingActivation(activated)
    }

    private fun cleanupUnreferenced(
        state: ReferenceStoreState,
        extraKeep: Set<ReferenceVersion> = emptySet(),
    ) {
        val keep = (setOfNotNull(state.active, state.previous, state.pending) + extraKeep)
            .map { fileFor(it).name }
            .toSet()
        root.listFiles()?.forEach { file ->
            if (file.isFile && file.name.startsWith("mobile-") && file.name.endsWith(".sqlite") && file.name !in keep) {
                file.delete()
            }
        }
    }

    private data class StartupVerification(
        val valid: Boolean,
        val seal: ReferenceFileSeal? = null,
    )

    private fun verifyForStartup(
        version: ReferenceVersion,
        storedSeal: ReferenceFileSeal?,
        expectedContractMajor: Int,
    ): StartupVerification {
        if (version.contractMajor != expectedContractMajor) return StartupVerification(false)
        val file = fileFor(version)
        if (!file.isFile || file.length() != version.sizeBytes) return StartupVerification(false)
        // A file seal proves that immutable bytes are unchanged, not that a newer
        // APK still supports the old physical layout. Re-check the cheap runtime
        // capability boundary on every startup before trusting the seal shortcut.
        if (!isRuntimeCapabilityVerified(file, version)) return StartupVerification(false)
        val provider = fileSealProvider
        if (provider == null) return StartupVerification(isContentVerified(file, version))

        val currentSeal = provider.capture(file) ?: return StartupVerification(false)
        if (storedSeal != null && currentSeal == storedSeal && !currentSeal.writable) {
            return StartupVerification(true, storedSeal)
        }
        if (!isDatabaseVerified(file, version)) return StartupVerification(false)
        if (currentSeal.writable && !file.setReadOnly()) return StartupVerification(false)
        val refreshed = provider.capture(file) ?: return StartupVerification(false)
        if (refreshed.writable) return StartupVerification(false)
        return StartupVerification(true, refreshed)
    }

    private fun captureVerifiedSeal(file: File): ReferenceFileSeal? {
        val provider = fileSealProvider ?: return null
        val seal = requireNotNull(provider.capture(file)) { "cannot capture verified reference file seal" }
        check(!seal.writable) { "verified reference file must be read-only" }
        return seal
    }

    private fun isContentVerified(version: ReferenceVersion): Boolean =
        isContentVerified(fileFor(version), version)

    private fun isContentVerified(file: File, version: ReferenceVersion): Boolean {
        if (!file.isFile || file.length() != version.sizeBytes) return false
        return contentHasher.sha256(file) == version.sha256
    }

    private fun isDatabaseVerified(version: ReferenceVersion): Boolean =
        isDatabaseVerified(fileFor(version), version)

    private fun isDatabaseVerified(file: File, version: ReferenceVersion): Boolean {
        if (!isContentVerified(file, version)) return false
        return runCatching { databaseVerifier.verify(file, version) }.isSuccess
    }

    private fun isRuntimeCapabilityVerified(file: File, version: ReferenceVersion): Boolean =
        runCatching { databaseVerifier.verifyRuntimeCapabilities(file, version) }.isSuccess

    private fun writeState(state: ReferenceStoreState) {
        stateStorage.write(ReferenceStateCodec.encode(state))
    }
}
