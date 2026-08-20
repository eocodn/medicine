package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
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
        schemaVersion: String = "10",
    ): ReferenceVersion = ReferenceVersion(
        datasetId = "sha256:" + MessageDigest.getInstance("SHA-256")
            .digest(dataset.toByteArray())
            .joinToString("") { "%02x".format(it) },
        sha256 = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) },
        sizeBytes = data.size.toLong(),
        schemaVersion = schemaVersion,
        releaseSequence = sequence,
    )

    private fun legacyState(active: ReferenceVersion, highWater: Long): ByteArray {
        val bytes = ByteArrayOutputStream()
        DataOutputStream(bytes).use { output ->
            output.writeUTF("MEDREFSTATE1")
            output.writeLong(highWater)
            output.writeBoolean(true)
            output.writeUTF(active.datasetId)
            output.writeUTF(active.sha256)
            output.writeLong(active.sizeBytes)
            output.writeUTF(active.schemaVersion)
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
            val selected = ReferenceStore(root, storage, FakeDatabaseVerifier()).openForStartup("10")
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
            assertEquals(initial, ReferenceStore(root, storage, verifier).openForStartup("10")!!.version)
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

            val activated = ReferenceStore(root, storage, verifier).openForStartup("10")!!
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
            ReferenceStore(root, storage, verifier).openForStartup("10")

            ReferenceStore(root, storage, verifier).fileFor(seven).apply {
                assertTrue(setWritable(true))
                writeText("invalid-corruption")
            }
            val recovered = ReferenceStore(root, storage, verifier).openForStartup("10")!!

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

            ReferenceStore(root, storage, verifier).openForStartup("10")

            assertEquals(callsAfterInstall, verifier.calls)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun establishedLkgWithMatchingFileSealSkipsFullContentHashOnStartup() {
        val root = Files.createTempDirectory("reference-store-sealed-fast").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val hasher = CountingContentHasher()
            val bytes = "release-sealed".toByteArray()
            val current = version(bytes, 3, "sealed")
            val store = ReferenceStore(root, storage, verifier, fileSealProvider = TestFileSealProvider(), contentHasher = hasher)
            store.installInitial(
                current,
                File(root, ".sealed.sqlite").apply { writeBytes(bytes) },
            )
            val hashesAfterInstall = hasher.calls

            val reopened = ReferenceStore(root, storage, verifier, fileSealProvider = TestFileSealProvider(), contentHasher = hasher)
                .openForStartup("10")

            assertEquals(current, reopened!!.version)
            assertEquals(hashesAfterInstall, hasher.calls)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun changedFileSealFallsBackToFullVerificationBeforeStartupUse() {
        val root = Files.createTempDirectory("reference-store-sealed-recheck").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val hasher = CountingContentHasher()
            val bytes = "release-sealed".toByteArray()
            val current = version(bytes, 3, "sealed")
            val store = ReferenceStore(root, storage, verifier, fileSealProvider = TestFileSealProvider(), contentHasher = hasher)
            store.installInitial(
                current,
                File(root, ".sealed.sqlite").apply { writeBytes(bytes) },
            )
            val target = store.fileFor(current)
            val hashesAfterInstall = hasher.calls
            assertTrue(target.setWritable(true))
            target.setLastModified(target.lastModified() + 2000L)
            assertTrue(target.setReadOnly())

            val reopened = ReferenceStore(root, storage, verifier, fileSealProvider = TestFileSealProvider(), contentHasher = hasher)
                .openForStartup("10")

            assertEquals(current, reopened!!.version)
            assertTrue(hasher.calls > hashesAfterInstall)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun legacyStatePerformsOneFullVerificationThenUsesSealedStartupFastPath() {
        val root = Files.createTempDirectory("reference-store-seal-migration").toFile()
        try {
            val verifier = FakeDatabaseVerifier()
            val hasher = CountingContentHasher()
            val sealProvider = TestFileSealProvider()
            val bytes = "legacy-reference".toByteArray()
            val current = version(bytes, 5, "legacy")
            val storage = MemoryStateStorage().apply { this.bytes = legacyState(current, 5) }
            val target = File(root, "mobile-${current.sha256}.sqlite").apply {
                writeBytes(bytes)
                assertTrue(setReadOnly())
            }

            ReferenceStore(
                root, storage, verifier,
                fileSealProvider = sealProvider,
                contentHasher = hasher,
            ).openForStartup("10")
            val hashesAfterMigration = hasher.calls
            val verifierCallsAfterMigration = verifier.calls

            val reopened = ReferenceStore(
                root, storage, verifier,
                fileSealProvider = sealProvider,
                contentHasher = hasher,
            ).openForStartup("10")

            assertEquals(current, reopened!!.version)
            assertTrue(target.isFile)
            assertTrue(hashesAfterMigration > 0)
            assertTrue(verifierCallsAfterMigration > 0)
            assertEquals(hashesAfterMigration, hasher.calls)
            assertEquals(verifierCallsAfterMigration, verifier.calls)
            assertNotNull(ReferenceStore(root, storage, verifier).snapshot().activeSeal)
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
            val old = version(bytes, 4, "old", schemaVersion = "9")
            ReferenceStore(root, storage, verifier).installInitial(
                old,
                File(root, ".old.sqlite").apply { writeBytes(bytes) },
            )

            assertNull(ReferenceStore(root, storage, verifier).openForStartup("10"))
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
                ReferenceStore(root, storage, FakeDatabaseVerifier()).openForStartup("10")
            }.exceptionOrNull()

            assertNotNull(error)
            assertTrue(error is IllegalArgumentException)
            assertTrue(error!!.message!!.contains("invalid reference state"))
        } finally {
            root.deleteRecursively()
        }
    }
}
