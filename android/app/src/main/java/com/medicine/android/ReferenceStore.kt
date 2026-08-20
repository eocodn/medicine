package com.medicine.android

import android.util.AtomicFile
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
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

data class ReferenceStoreState(
    val active: ReferenceVersion? = null,
    val previous: ReferenceVersion? = null,
    val pending: ReferenceVersion? = null,
    val highestActivatedSequence: Long = 0,
    val highestSeenRootSequence: Long = 0,
    val highestSeenRootHash: String? = null,
    val highestRetiredContractMajor: Int = 0,
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
}

class ReferenceStore(
    private val root: File,
    private val stateStorage: ReferenceStateStorage,
    private val databaseVerifier: ReferenceDatabaseVerifier,
) {
    init {
        check(root.exists() || root.mkdirs()) { "cannot create reference data directory" }
    }

    fun fileFor(version: ReferenceVersion): File = File(root, "mobile-${version.sha256}.sqlite")

    fun snapshot(): ReferenceStoreState = decodeState(stateStorage.read())

    fun openForStartup(expectedContractMajor: Int): InstalledReferenceVersion? {
        require(expectedContractMajor > 0) { "invalid expected reference contract major" }
        var recoveryReason: String? = null
        var state = snapshot()

        val activation = activatePendingIfValid(state, expectedContractMajor)
        state = activation.state
        recoveryReason = recoveryReason ?: activation.recoveryReason

        // An established LKG was runtime-verified before installation, so normal
        // startup only rechecks immutable content identity. Schema compatibility is
        // still checked before exposing it to the current embedded Python runtime.
        fun startupCompatible(version: ReferenceVersion): Boolean =
            version.contractMajor == expectedContractMajor && isContentVerified(version)

        val selected = when {
            state.active != null && startupCompatible(state.active) -> state.active
            state.previous != null && startupCompatible(state.previous) -> {
                recoveryReason = recoveryReason ?: "active reference invalid; using previous LKG"
                state.previous
            }
            else -> null
        }
        if (selected == null) {
            return null
        }

        val normalized = if (selected == state.active) {
            state
        } else {
            state.copy(active = selected, previous = null)
        }
        if (normalized != state) writeState(normalized)
        cleanupUnreferenced(normalized)
        return InstalledReferenceVersion(selected, fileFor(selected), recoveryReason)
    }

    fun cleanupForBootstrap(version: ReferenceVersion) {
        cleanupUnreferenced(snapshot(), extraKeep = setOf(version))
    }

    fun observeSignedRoot(releaseSequence: Long, rootHash: String) {
        require(releaseSequence > 0) { "signed root sequence must be positive" }
        require(rootHash.matches(Regex("[0-9a-f]{64}"))) { "invalid signed root hash" }
        val state = snapshot()
        when {
            releaseSequence < state.highestSeenRootSequence ->
                throw IllegalArgumentException("signed reference root rollback is not allowed")
            releaseSequence == state.highestSeenRootSequence ->
                require(state.highestSeenRootHash == rootHash) {
                    "signed reference root changed without advancing sequence"
                }
            else -> writeState(
                state.copy(
                    highestSeenRootSequence = releaseSequence,
                    highestSeenRootHash = rootHash,
                )
            )
        }
    }

    fun markContractRetired(contractMajor: Int, releaseSequence: Long, rootHash: String) {
        require(contractMajor > 0) { "retired reference contract major must be positive" }
        observeSignedRoot(releaseSequence, rootHash)
        val state = snapshot()
        if (contractMajor <= state.highestRetiredContractMajor) return
        writeState(state.copy(highestRetiredContractMajor = contractMajor))
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

        val activated = ReferenceStoreState(
            active = version,
            previous = null,
            pending = null,
            highestActivatedSequence = maxOf(state.highestActivatedSequence, version.releaseSequence),
            highestSeenRootSequence = state.highestSeenRootSequence,
            highestSeenRootHash = state.highestSeenRootHash,
            highestRetiredContractMajor = state.highestRetiredContractMajor,
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

        writeState(state.copy(pending = version))
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
        if (pending.contractMajor != expectedContractMajor || !isDatabaseVerified(pending)) {
            fileFor(pending).delete()
            val cleared = state.copy(pending = null)
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
            val cleared = state.copy(pending = null)
            writeState(cleared)
            return PendingActivation(
                cleared,
                "pending reference sequence is no longer eligible; retained current LKG",
            )
        }

        val validCurrent = state.active?.takeIf { it != pending && isContentVerified(it) }
            ?: state.previous?.takeIf { it != pending && isContentVerified(it) }
        val activated = ReferenceStoreState(
            active = pending,
            previous = validCurrent,
            pending = null,
            highestActivatedSequence = maxOf(state.highestActivatedSequence, pending.releaseSequence),
            highestSeenRootSequence = state.highestSeenRootSequence,
            highestSeenRootHash = state.highestSeenRootHash,
            highestRetiredContractMajor = state.highestRetiredContractMajor,
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

    private fun isContentVerified(version: ReferenceVersion): Boolean =
        isContentVerified(fileFor(version), version)

    private fun isContentVerified(file: File, version: ReferenceVersion): Boolean {
        if (!file.isFile || file.length() != version.sizeBytes) return false
        return sha256(file) == version.sha256
    }

    private fun isDatabaseVerified(version: ReferenceVersion): Boolean =
        isDatabaseVerified(fileFor(version), version)

    private fun isDatabaseVerified(file: File, version: ReferenceVersion): Boolean {
        if (!isContentVerified(file, version)) return false
        return runCatching { databaseVerifier.verify(file, version) }.isSuccess
    }

    private fun writeState(state: ReferenceStoreState) {
        stateStorage.write(encodeState(state))
    }

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

    companion object {
        private const val STATE_MAGIC = "MEDREFSTATE2"

        private fun encodeState(state: ReferenceStoreState): ByteArray {
            val bytes = ByteArrayOutputStream()
            DataOutputStream(bytes).use { output ->
                output.writeUTF(STATE_MAGIC)
                output.writeLong(state.highestActivatedSequence)
                output.writeLong(state.highestSeenRootSequence)
                output.writeUTF(state.highestSeenRootHash ?: "")
                output.writeInt(state.highestRetiredContractMajor)
                writeVersion(output, state.active)
                writeVersion(output, state.previous)
                writeVersion(output, state.pending)
            }
            return bytes.toByteArray()
        }

        private fun decodeState(bytes: ByteArray?): ReferenceStoreState {
            if (bytes == null) return ReferenceStoreState()
            try {
                DataInputStream(ByteArrayInputStream(bytes)).use { input ->
                    require(input.readUTF() == STATE_MAGIC) { "unsupported reference state format" }
                    val highWater = input.readLong()
                    val rootHighWater = input.readLong()
                    val rootHash = input.readUTF().ifEmpty { null }
                    val retiredContractMajor = input.readInt()
                    val state = ReferenceStoreState(
                        active = readVersion(input),
                        previous = readVersion(input),
                        pending = readVersion(input),
                        highestActivatedSequence = highWater,
                        highestSeenRootSequence = rootHighWater,
                        highestSeenRootHash = rootHash,
                        highestRetiredContractMajor = retiredContractMajor,
                    )
                    require(input.read() == -1) { "trailing reference state data" }
                    return state
                }
            } catch (error: Exception) {
                throw IllegalArgumentException("invalid reference state", error)
            }
        }

        private fun writeVersion(output: DataOutputStream, version: ReferenceVersion?) {
            output.writeBoolean(version != null)
            if (version == null) return
            output.writeUTF(version.datasetId)
            output.writeUTF(version.sha256)
            output.writeLong(version.sizeBytes)
            output.writeInt(version.contractMajor)
            output.writeLong(version.releaseSequence)
        }

        private fun readVersion(input: DataInputStream): ReferenceVersion? {
            if (!input.readBoolean()) return null
            return ReferenceVersion(
                datasetId = input.readUTF(),
                sha256 = input.readUTF(),
                sizeBytes = input.readLong(),
                contractMajor = input.readInt(),
                releaseSequence = input.readLong(),
            )
        }
    }
}
