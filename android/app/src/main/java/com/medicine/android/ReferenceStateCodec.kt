package com.medicine.android

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream


internal object ReferenceStateCodec {
    private const val STATE_MAGIC_V1 = "MEDREFSTATE1"
    private const val STATE_MAGIC_V3 = "MEDREFSTATE3"
    private const val LEGACY_SCHEMA_VERSION = "10"
    private const val LEGACY_CONTRACT_MAJOR = 1

    fun encode(state: ReferenceStoreState): ByteArray {
        val bytes = ByteArrayOutputStream()
        DataOutputStream(bytes).use { output ->
            output.writeUTF(STATE_MAGIC_V3)
            output.writeLong(state.highestActivatedSequence)
            output.writeLong(state.highestSeenRootSequence)
            output.writeUTF(state.highestSeenRootHash ?: "")
            output.writeInt(state.highestRetiredContractMajor)
            writeVersion(output, state.active)
            writeSeal(output, state.activeSeal)
            writeVersion(output, state.previous)
            writeSeal(output, state.previousSeal)
            writeVersion(output, state.pending)
            writeSeal(output, state.pendingSeal)
        }
        return bytes.toByteArray()
    }

    fun decode(bytes: ByteArray?): ReferenceStoreState {
        if (bytes == null) return ReferenceStoreState()
        try {
            DataInputStream(ByteArrayInputStream(bytes)).use { input ->
                val magic = input.readUTF()
                require(magic == STATE_MAGIC_V1 || magic == STATE_MAGIC_V3) {
                    "unsupported reference state format"
                }
                val highWater = input.readLong()
                val state = if (magic == STATE_MAGIC_V1) {
                    ReferenceStoreState(
                        active = readLegacyVersion(input),
                        previous = readLegacyVersion(input),
                        pending = readLegacyVersion(input),
                        highestActivatedSequence = highWater,
                    )
                } else {
                    val rootHighWater = input.readLong()
                    val rootHash = input.readUTF().ifEmpty { null }
                    val retiredContractMajor = input.readInt()
                    val active = readVersion(input)
                    val activeSeal = readSeal(input)
                    val previous = readVersion(input)
                    val previousSeal = readSeal(input)
                    val pending = readVersion(input)
                    val pendingSeal = readSeal(input)
                    ReferenceStoreState(
                        active = active,
                        previous = previous,
                        pending = pending,
                        highestActivatedSequence = highWater,
                        highestSeenRootSequence = rootHighWater,
                        highestSeenRootHash = rootHash,
                        highestRetiredContractMajor = retiredContractMajor,
                        activeSeal = activeSeal,
                        previousSeal = previousSeal,
                        pendingSeal = pendingSeal,
                    )
                }
                require(input.read() == -1) { "trailing reference state data" }
                return state
            }
        } catch (error: Exception) {
            throw IllegalArgumentException("invalid reference state", error)
        }
    }

    fun isLegacyV1(bytes: ByteArray?): Boolean = stateMagic(bytes) == STATE_MAGIC_V1

    private fun stateMagic(bytes: ByteArray?): String? {
        if (bytes == null) return null
        return runCatching {
            DataInputStream(ByteArrayInputStream(bytes)).use { input -> input.readUTF() }
        }.getOrNull()
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

    private fun readLegacyVersion(input: DataInputStream): ReferenceVersion? {
        if (!input.readBoolean()) return null
        val datasetId = input.readUTF()
        val sha256 = input.readUTF()
        val sizeBytes = input.readLong()
        val schemaVersion = input.readUTF()
        require(schemaVersion == LEGACY_SCHEMA_VERSION) {
            "unsupported legacy reference schema version"
        }
        return ReferenceVersion(
            datasetId = datasetId,
            sha256 = sha256,
            sizeBytes = sizeBytes,
            contractMajor = LEGACY_CONTRACT_MAJOR,
            releaseSequence = input.readLong(),
        )
    }

    private fun writeSeal(output: DataOutputStream, seal: ReferenceFileSeal?) {
        output.writeBoolean(seal != null)
        if (seal == null) return
        output.writeLong(seal.sizeBytes)
        output.writeLong(seal.modifiedMarker)
        output.writeLong(seal.changedMarker)
        output.writeUTF(seal.identityKey)
        output.writeBoolean(seal.writable)
    }

    private fun readSeal(input: DataInputStream): ReferenceFileSeal? {
        if (!input.readBoolean()) return null
        return ReferenceFileSeal(
            sizeBytes = input.readLong(),
            modifiedMarker = input.readLong(),
            changedMarker = input.readLong(),
            identityKey = input.readUTF(),
            writable = input.readBoolean(),
        )
    }
}