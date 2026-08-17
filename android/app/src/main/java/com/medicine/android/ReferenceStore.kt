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
    val schemaVersion: String,
    val releaseSequence: Long,
) {
    init {
        require(DATASET_ID.matches(datasetId)) { "invalid reference dataset id" }
        require(SHA256.matches(sha256)) { "invalid reference SHA-256" }
        require(sizeBytes > 0) { "invalid reference size" }
        require(schemaVersion.matches(Regex("[1-9][0-9]*"))) { "invalid reference schema version" }
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
) {
    init {
        require(highestActivatedSequence >= 0) { "invalid reference activation sequence" }
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

    fun openForStartup(
        bundled: ReferenceVersion,
        installBundled: (File) -> Unit,
    ): InstalledReferenceVersion {
        require(bundled.releaseSequence == 0L) { "bundled reference must use release sequence zero" }
        var recoveryReason: String? = null
        var state = try {
            snapshot()
        } catch (_: Exception) {
            // A corrupted state file makes the last accepted release sequence unknowable.
            // Keep the app usable from its bundled DB, but fail closed for future network
            // updates rather than silently resetting anti-rollback state to zero.
            recoveryReason = "reference state invalid; network updates are blocked until app data is reset"
            ReferenceStoreState(highestActivatedSequence = Long.MAX_VALUE).also(::writeState)
        }

        val activation = activatePendingIfValid(state)
        state = activation.state
        recoveryReason = recoveryReason ?: activation.recoveryReason

        val selected = when {
            state.active != null && isContentVerified(state.active) -> state.active
            state.previous != null && isContentVerified(state.previous) -> {
                recoveryReason = recoveryReason ?: "active reference invalid; using previous LKG"
                state.previous
            }
            else -> {
                if (state.active != null || state.previous != null) {
                    recoveryReason = recoveryReason ?: "installed reference LKG invalid; using bundled fallback"
                }
                ensureBundled(bundled, installBundled)
                bundled
            }
        }
        check(isContentVerified(selected)) { "no verified reference database is available" }

        val normalized = if (selected == state.active) {
            state
        } else {
            state.copy(active = selected, previous = null)
        }
        if (normalized != state) writeState(normalized)
        cleanupUnreferenced(normalized, bundled)
        return InstalledReferenceVersion(selected, fileFor(selected), recoveryReason)
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

    private fun ensureBundled(bundled: ReferenceVersion, installBundled: (File) -> Unit) {
        val target = fileFor(bundled)
        if (isContentVerified(target, bundled)) {
            check(runCatching { databaseVerifier.verify(target, bundled) }.isSuccess) {
                "bundled reference runtime verification failed"
            }
            return
        }
        if (target.exists()) check(target.delete()) { "cannot remove invalid bundled reference copy" }
        val temporary = File(root, ".bundled-${bundled.sha256}.tmp")
        temporary.delete()
        try {
            installBundled(temporary)
            check(isDatabaseVerified(temporary, bundled)) { "bundled reference verification failed" }
            check(temporary.renameTo(target)) { "cannot atomically install bundled reference" }
            check(target.setReadOnly()) { "cannot make bundled reference read-only" }
            check(isContentVerified(target, bundled)) { "installed bundled reference verification failed" }
        } finally {
            temporary.delete()
        }
    }

    private data class PendingActivation(
        val state: ReferenceStoreState,
        val recoveryReason: String? = null,
    )

    private fun activatePendingIfValid(state: ReferenceStoreState): PendingActivation {
        val pending = state.pending ?: return PendingActivation(state)
        if (!isDatabaseVerified(pending)) {
            fileFor(pending).delete()
            val cleared = state.copy(pending = null)
            writeState(cleared)
            return PendingActivation(
                cleared,
                "pending reference failed re-verification; retained current LKG",
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
        )
        writeState(activated)
        return PendingActivation(activated)
    }

    private fun cleanupUnreferenced(state: ReferenceStoreState, bundled: ReferenceVersion) {
        val keep = setOfNotNull(state.active, state.previous, state.pending, bundled)
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
        private const val STATE_MAGIC = "MEDREFSTATE1"

        private fun encodeState(state: ReferenceStoreState): ByteArray {
            val bytes = ByteArrayOutputStream()
            DataOutputStream(bytes).use { output ->
                output.writeUTF(STATE_MAGIC)
                output.writeLong(state.highestActivatedSequence)
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
                    val state = ReferenceStoreState(
                        active = readVersion(input),
                        previous = readVersion(input),
                        pending = readVersion(input),
                        highestActivatedSequence = highWater,
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
            output.writeUTF(version.schemaVersion)
            output.writeLong(version.releaseSequence)
        }

        private fun readVersion(input: DataInputStream): ReferenceVersion? {
            if (!input.readBoolean()) return null
            return ReferenceVersion(
                datasetId = input.readUTF(),
                sha256 = input.readUTF(),
                sizeBytes = input.readLong(),
                schemaVersion = input.readUTF(),
                releaseSequence = input.readLong(),
            )
        }
    }
}
