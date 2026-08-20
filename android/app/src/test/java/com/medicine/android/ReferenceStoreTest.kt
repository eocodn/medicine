package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest

class ReferenceStoreTest {
    private class MemoryStateStorage : ReferenceStateStorage {
        var bytes: ByteArray? = null
        override fun read(): ByteArray? = bytes?.copyOf()
        override fun write(value: ByteArray) { bytes = value.copyOf() }
    }

    private class CountingStateStorage : ReferenceStateStorage {
        var bytes: ByteArray? = null
        var writes = 0
        override fun read(): ByteArray? = bytes?.copyOf()
        override fun write(value: ByteArray) {
            writes += 1
            bytes = value.copyOf()
        }
    }

    private class FakeDatabaseVerifier : ReferenceDatabaseVerifier {
        var calls = 0
        override fun verify(file: File, version: ReferenceVersion) {
            calls += 1
            require(!file.readText().startsWith("invalid")) { "runtime database invalid" }
        }
    }

    private class CountingContentHasher : ReferenceContentHasher {
        var calls = 0
        override fun sha256(file: File): String {
            calls += 1
            return MessageDigest.getInstance("SHA-256")
                .digest(file.readBytes())
                .joinToString("") { "%02x".format(it) }
        }
    }

    private class TestFileSealProvider : ReferenceFileSealProvider {
        override fun capture(file: File): ReferenceFileSeal? {
            if (!file.isFile) return null
            return ReferenceFileSeal(
                sizeBytes = file.length(),
                modifiedMarker = file.lastModified(),
                changedMarker = file.lastModified(),
                identityKey = file.canonicalPath,
                writable = file.canWrite(),
            )
        }
    }

    private fun version(
        data: ByteArray,
        sequence: Long,
        dataset: String,
        contractMajor: Int = 1,
    ): ReferenceVersion = ReferenceVersion(
        datasetId = "sha256:" + MessageDigest.getInstance("SHA-256")
            .digest(dataset.toByteArray())
            .joinToString("") { "%02x".format(it) },
        sha256 = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) },
        sizeBytes = data.size.toLong(),
        contractMajor = contractMajor,
        releaseSequence = sequence,
    )

    private fun currentMainV1State(active: ReferenceVersion, highWater: Long): ByteArray {
        val bytes = ByteArrayOutputStream()
        DataOutputStream(bytes).use { output ->
            output.writeUTF("MEDREFSTATE1")
            output.writeLong(highWater)
            output.writeBoolean(true)
            output.writeUTF(active.datasetId)
            output.writeUTF(active.sha256)
            output.writeLong(active.sizeBytes)
            output.writeUTF("10")
            output.writeLong(active.releaseSequence)
            output.writeBoolean(false)
            output.writeBoolean(false)
        }
        return bytes.toByteArray()
    }

    @Test
    fun emptyStoreHasNoStartupReferenceWithoutBundledFallback() {
        val root = Files.createTempDirectory("reference-store-empty").toFile()
        try {
            val storage = MemoryStateStorage()
            val selected = ReferenceStore(root, storage, FakeDatabaseVerifier()).openForStartup(1)
            assertNull(selected)
            assertEquals(0, ReferenceStore(root, storage, FakeDatabaseVerifier()).snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun initialNetworkReferenceBecomesActiveImmediatelyAndSetsHighWater() {
        val root = Files.createTempDirectory("reference-store-initial").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bytes = "network-initial".toByteArray()
            val initial = version(bytes, 12, "network-initial")
            val store = ReferenceStore(root, storage, verifier)

            val installed = store.installInitial(
                initial,
                File(root, ".candidate-initial.sqlite").apply { writeBytes(bytes) },
            )

            assertEquals(initial, installed.version)
            assertEquals(initial, store.snapshot().active)
            assertNull(store.snapshot().previous)
            assertNull(store.snapshot().pending)
            assertEquals(12, store.snapshot().highestActivatedSequence)
            assertEquals(initial, ReferenceStore(root, storage, verifier).openForStartup(1)!!.version)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun initialInstallAdoptsAlreadyVerifiedContentAddressedTargetAfterInterruptedStateWrite() {
        val root = Files.createTempDirectory("reference-store-adopt").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bytes = "network-adopt".toByteArray()
            val initial = version(bytes, 14, "network-adopt")
            val store = ReferenceStore(root, storage, verifier)
            store.fileFor(initial).writeBytes(bytes)

            val installed = store.installInitial(initial, candidate = null)

            assertEquals(initial, installed.version)
            assertEquals(14, store.snapshot().highestActivatedSequence)
            assertEquals(initial, store.snapshot().active)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun pendingReleaseActivatesOnlyOnNextStartupAndKeepsPreviousLkg() {
        val root = Files.createTempDirectory("reference-store-pending").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val currentBytes = "release-one".toByteArray()
            val current = version(currentBytes, 1, "one")
            val store = ReferenceStore(root, storage, verifier)
            store.installInitial(current, File(root, ".one.sqlite").apply { writeBytes(currentBytes) })

            val updateBytes = "release-seven".toByteArray()
            val update = version(updateBytes, 7, "seven")
            store.stagePending(update, File(root, ".seven.sqlite").apply { writeBytes(updateBytes) })

            assertEquals(current, store.snapshot().active)
            assertEquals(update, store.snapshot().pending)
            assertEquals(1, store.snapshot().highestActivatedSequence)

            val activated = ReferenceStore(root, storage, verifier).openForStartup(1)!!
            assertEquals(update, activated.version)
            assertEquals(update, ReferenceStore(root, storage, verifier).snapshot().active)
            assertEquals(current, ReferenceStore(root, storage, verifier).snapshot().previous)
            assertEquals(7, ReferenceStore(root, storage, verifier).snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun corruptedActiveFallsBackToPreviousWithoutLoweringHighWater() {
        val root = Files.createTempDirectory("reference-store-fallback").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val currentBytes = "release-one".toByteArray()
            val current = version(currentBytes, 1, "one")
            val sevenBytes = "release-seven".toByteArray()
            val seven = version(sevenBytes, 7, "seven")
            val store = ReferenceStore(root, storage, verifier)
            store.installInitial(current, File(root, ".one.sqlite").apply { writeBytes(currentBytes) })
            store.stagePending(seven, File(root, ".seven.sqlite").apply { writeBytes(sevenBytes) })
            ReferenceStore(root, storage, verifier).openForStartup(1)

            ReferenceStore(root, storage, verifier).fileFor(seven).apply {
                assertTrue(setWritable(true))
                writeText("invalid-corruption")
            }
            val recovered = ReferenceStore(root, storage, verifier).openForStartup(1)!!

            assertEquals(current, recovered.version)
            assertTrue(recovered.recoveryReason!!.contains("previous"))
            assertEquals(7, ReferenceStore(root, storage, verifier).snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun establishedLkgUsesContentVerificationWithoutRepeatingRuntimeCheck() {
        val root = Files.createTempDirectory("reference-store-lkg-fast").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bytes = "release-one".toByteArray()
            val current = version(bytes, 1, "one")
            ReferenceStore(root, storage, verifier).installInitial(
                current,
                File(root, ".one.sqlite").apply { writeBytes(bytes) },
            )
            val callsAfterInstall = verifier.calls

            ReferenceStore(root, storage, verifier).openForStartup(1)

            assertEquals(callsAfterInstall, verifier.calls)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun matchingVerifiedFileSealSkipsFullHashOnOrdinaryContractStartup() {
        val root = Files.createTempDirectory("reference-store-sealed-fast").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val hasher = CountingContentHasher()
            val bytes = "release-sealed".toByteArray()
            val current = version(bytes, 3, "sealed", contractMajor = 1)
            val store = ReferenceStore(
                root,
                storage,
                verifier,
                fileSealProvider = TestFileSealProvider(),
                contentHasher = hasher,
            )
            store.installInitial(current, File(root, ".sealed.sqlite").apply { writeBytes(bytes) })
            val hashesAfterInstall = hasher.calls
            val verifierCallsAfterInstall = verifier.calls

            val reopened = ReferenceStore(
                root,
                storage,
                verifier,
                fileSealProvider = TestFileSealProvider(),
                contentHasher = hasher,
            ).openForStartup(1)

            assertEquals(current, reopened!!.version)
            assertEquals(hashesAfterInstall, hasher.calls)
            assertEquals(verifierCallsAfterInstall, verifier.calls)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun changedFileSealFallsBackToFullContractVerification() {
        val root = Files.createTempDirectory("reference-store-sealed-recheck").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val hasher = CountingContentHasher()
            val bytes = "release-sealed".toByteArray()
            val current = version(bytes, 3, "sealed", contractMajor = 1)
            val store = ReferenceStore(
                root,
                storage,
                verifier,
                fileSealProvider = TestFileSealProvider(),
                contentHasher = hasher,
            )
            store.installInitial(current, File(root, ".sealed.sqlite").apply { writeBytes(bytes) })
            val target = store.fileFor(current)
            val hashesAfterInstall = hasher.calls
            val verifierCallsAfterInstall = verifier.calls
            assertTrue(target.setWritable(true))
            target.setLastModified(target.lastModified() + 2000L)
            assertTrue(target.setReadOnly())

            val reopened = ReferenceStore(
                root,
                storage,
                verifier,
                fileSealProvider = TestFileSealProvider(),
                contentHasher = hasher,
            ).openForStartup(1)

            assertEquals(current, reopened!!.version)
            assertTrue(hasher.calls > hashesAfterInstall)
            assertTrue(verifier.calls > verifierCallsAfterInstall)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun currentMainV1StateFullVerifiesOnceThenMigratesToIntegratedV3Seal() {
        val root = Files.createTempDirectory("reference-store-v1-migration").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val hasher = CountingContentHasher()
            val sealProvider = TestFileSealProvider()
            val bytes = "legacy-reference".toByteArray()
            val current = version(bytes, 5, "legacy", contractMajor = 1)
            storage.bytes = currentMainV1State(current, 5)
            File(root, "mobile-${current.sha256}.sqlite").apply {
                writeBytes(bytes)
                assertTrue(setReadOnly())
            }

            val first = ReferenceStore(
                root,
                storage,
                verifier,
                fileSealProvider = sealProvider,
                contentHasher = hasher,
            ).openForStartup(1)
            val hashesAfterMigration = hasher.calls
            val verifierCallsAfterMigration = verifier.calls
            val migrated = ReferenceStore(root, storage, verifier).snapshot()

            val second = ReferenceStore(
                root,
                storage,
                verifier,
                fileSealProvider = sealProvider,
                contentHasher = hasher,
            ).openForStartup(1)

            assertEquals(current, first!!.version)
            assertEquals(current, second!!.version)
            assertTrue(hashesAfterMigration > 0)
            assertTrue(verifierCallsAfterMigration > 0)
            assertEquals(hashesAfterMigration, hasher.calls)
            assertEquals(verifierCallsAfterMigration, verifier.calls)
            assertNotNull(migrated.activeSeal)
            assertEquals(0, migrated.highestSeenRootSequence)
            assertEquals(0, migrated.highestRetiredContractMajor)
            DataInputStream(ByteArrayInputStream(storage.bytes)).use { input ->
                assertEquals("MEDREFSTATE3", input.readUTF())
            }
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun incompatibleInstalledSchemaDoesNotBecomeCurrentRuntimeLkg() {
        val root = Files.createTempDirectory("reference-store-schema").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bytes = "release-old-schema".toByteArray()
            val old = version(bytes, 4, "old", contractMajor = 2)
            ReferenceStore(root, storage, verifier).installInitial(
                old,
                File(root, ".old.sqlite").apply { writeBytes(bytes) },
            )

            assertNull(ReferenceStore(root, storage, verifier).openForStartup(1))
            assertEquals(4, ReferenceStore(root, storage, verifier).snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun corruptStateFailsClosedInsteadOfResettingAntiRollbackState() {
        val root = Files.createTempDirectory("reference-store-state").toFile()
        try {
            val storage = MemoryStateStorage().apply { bytes = "corrupt-state".toByteArray() }
            val error = runCatching {
                ReferenceStore(root, storage, FakeDatabaseVerifier()).openForStartup(1)
            }.exceptionOrNull()

            assertNotNull(error)
            assertTrue(error is IllegalArgumentException)
            assertTrue(error!!.message!!.contains("invalid reference state"))
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun signedRootHighWaterRejectsOlderSequenceAndSameSequenceDifferentRoot() {
        val root = Files.createTempDirectory("reference-store-root-high-water").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val firstHash = "a".repeat(64)
            val otherHash = "b".repeat(64)

            store.observeSignedRoot(20, firstHash)
            store.observeSignedRoot(20, firstHash)

            assertEquals(20, store.snapshot().highestSeenRootSequence)
            assertEquals(firstHash, store.snapshot().highestSeenRootHash)
            assertTrue(runCatching { store.observeSignedRoot(19, firstHash) }.isFailure)
            assertTrue(runCatching { store.observeSignedRoot(20, otherHash) }.isFailure)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun observedContractRetirementPersistsAcrossRestartButDoesNotRetireNextContract() {
        val root = Files.createTempDirectory("reference-store-retired-contract").toFile()
        try {
            val storage = MemoryStateStorage()
            val signedRootHash = "d".repeat(64)
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())

            store.markContractRetired(1, 12, signedRootHash)

            val reopened = ReferenceStore(root, storage, FakeDatabaseVerifier())
            assertTrue(reopened.isContractRetired(1))
            assertFalse(reopened.isContractRetired(2))
            assertEquals(1, reopened.snapshot().highestRetiredContractMajor)
            assertEquals(12, reopened.snapshot().highestSeenRootSequence)
            assertEquals(signedRootHash, reopened.snapshot().highestSeenRootHash)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun observingContractRetirementPersistsRootHighWaterAndRetirementAtomically() {
        val root = Files.createTempDirectory("reference-store-retirement-atomic").toFile()
        try {
            val storage = CountingStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val signedRootHash = "e".repeat(64)

            store.markContractRetired(1, 13, signedRootHash)

            assertEquals(1, storage.writes)
            val state = store.snapshot()
            assertEquals(13, state.highestSeenRootSequence)
            assertEquals(signedRootHash, state.highestSeenRootHash)
            assertEquals(1, state.highestRetiredContractMajor)
        } finally {
            root.deleteRecursively()
        }
    }
}
