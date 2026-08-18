package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest

class ReferenceStoreTest {
    private class MemoryStateStorage : ReferenceStateStorage {
        var bytes: ByteArray? = null
        override fun read(): ByteArray? = bytes?.copyOf()
        override fun write(value: ByteArray) {
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

    private fun version(
        data: ByteArray,
        sequence: Long,
        dataset: String,
        schemaVersion: String = "8",
    ): ReferenceVersion = ReferenceVersion(
        datasetId = "sha256:" + MessageDigest.getInstance("SHA-256")
            .digest(dataset.toByteArray())
            .joinToString("") { "%02x".format(it) },
        sha256 = MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) },
        sizeBytes = data.size.toLong(),
        schemaVersion = schemaVersion,
        releaseSequence = sequence,
    )

    @Test
    fun pendingReleaseActivatesOnlyOnNextStartupAndKeepsPreviousLkg() {
        val root = Files.createTempDirectory("reference-store").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bundledBytes = "bundled".toByteArray()
            val bundled = version(bundledBytes, 0, "sha256:bundled")
            val first = ReferenceStore(root, storage, verifier)
            val startup = first.openForStartup(bundled) { target -> target.writeBytes(bundledBytes) }
            assertEquals(bundled, startup.version)

            val updateBytes = "release-seven".toByteArray()
            val update = version(updateBytes, 7, "sha256:seven")
            val candidate = File(root, ".candidate-seven.sqlite").apply { writeBytes(updateBytes) }
            first.stagePending(update, candidate)

            val staged = first.snapshot()
            assertEquals(bundled, staged.active)
            assertEquals(update, staged.pending)
            assertEquals(0, staged.highestActivatedSequence)

            val second = ReferenceStore(root, storage, verifier)
            val activated = second.openForStartup(bundled) { error("bundled copy should already exist") }
            assertEquals(update, activated.version)
            assertEquals(update, second.snapshot().active)
            assertEquals(bundled, second.snapshot().previous)
            assertNull(second.snapshot().pending)
            assertEquals(7, second.snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun corruptedActiveFallsBackButDoesNotLowerAntiRollbackHighWater() {
        val root = Files.createTempDirectory("reference-store-fallback").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bundledBytes = "bundled".toByteArray()
            val bundled = version(bundledBytes, 0, "sha256:bundled")
            val updateBytes = "release-seven".toByteArray()
            val update = version(updateBytes, 7, "sha256:seven")

            val first = ReferenceStore(root, storage, verifier)
            first.openForStartup(bundled) { it.writeBytes(bundledBytes) }
            first.stagePending(update, File(root, ".candidate.sqlite").apply { writeBytes(updateBytes) })
            ReferenceStore(root, storage, verifier).openForStartup(bundled) { error("bundled exists") }

            ReferenceStore(root, storage, verifier).fileFor(update).apply {
                assertTrue(setWritable(true))
                writeText("invalid-corruption")
            }
            val recoveredStore = ReferenceStore(root, storage, verifier)
            val recovered = recoveredStore.openForStartup(bundled) { error("bundled exists") }
            assertEquals(bundled, recovered.version)
            assertEquals(7, recoveredStore.snapshot().highestActivatedSequence)

            val oldBytes = "release-six".toByteArray()
            val old = version(oldBytes, 6, "sha256:six")
            val oldCandidate = File(root, ".candidate-six.sqlite").apply { writeBytes(oldBytes) }
            val error = runCatching { recoveredStore.stagePending(old, oldCandidate) }.exceptionOrNull()
            assertTrue(error is IllegalArgumentException)
            assertTrue(error!!.message!!.contains("rollback"))
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun sameHighWaterReleaseCanRepairLocalCorruptionWithoutPermittingOlderReplay() {
        val root = Files.createTempDirectory("reference-store-repair").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bundledBytes = "bundled".toByteArray()
            val bundled = version(bundledBytes, 0, "sha256:bundled")
            val updateBytes = "release-seven".toByteArray()
            val update = version(updateBytes, 7, "sha256:seven")

            val first = ReferenceStore(root, storage, verifier)
            first.openForStartup(bundled) { it.writeBytes(bundledBytes) }
            first.stagePending(update, File(root, ".candidate.sqlite").apply { writeBytes(updateBytes) })
            val activeStore = ReferenceStore(root, storage, verifier)
            activeStore.openForStartup(bundled) { error("bundled exists") }
            activeStore.fileFor(update).apply {
                assertTrue(setWritable(true))
                writeText("invalid-corruption")
            }
            val recovered = ReferenceStore(root, storage, verifier)
            recovered.openForStartup(bundled) { error("bundled exists") }

            val repair = File(root, ".candidate-repair.sqlite").apply { writeBytes(updateBytes) }
            recovered.stagePending(update, repair)
            val repaired = ReferenceStore(root, storage, verifier)
            assertEquals(update, repaired.openForStartup(bundled) { error("bundled exists") }.version)
            assertEquals(7, repaired.snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }
    @Test
    fun establishedLkgUsesContentVerificationWithoutRepeatingFullRuntimeCheck() {
        val root = Files.createTempDirectory("reference-store-lkg-fast").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val bundledBytes = "bundled".toByteArray()
            val bundled = version(bundledBytes, 0, "bundled")

            ReferenceStore(root, storage, verifier).openForStartup(bundled) { it.writeBytes(bundledBytes) }
            assertEquals(1, verifier.calls)

            ReferenceStore(root, storage, verifier).openForStartup(bundled) { error("bundled exists") }
            assertEquals(1, verifier.calls)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun schemaUpgradeRejectsOldActiveLkgAndUsesBundledReference() {
        val root = Files.createTempDirectory("reference-store-schema-upgrade").toFile()
        try {
            val storage = MemoryStateStorage()
            val verifier = FakeDatabaseVerifier()
            val oldBundledBytes = "bundled-v8".toByteArray()
            val oldBundled = version(oldBundledBytes, 0, "bundled-v8", schemaVersion = "8")
            val oldReleaseBytes = "release-v8-seven".toByteArray()
            val oldRelease = version(oldReleaseBytes, 7, "release-v8-seven", schemaVersion = "8")

            val first = ReferenceStore(root, storage, verifier)
            first.openForStartup(oldBundled) { it.writeBytes(oldBundledBytes) }
            first.stagePending(
                oldRelease,
                File(root, ".candidate-v8.sqlite").apply { writeBytes(oldReleaseBytes) },
            )
            ReferenceStore(root, storage, verifier)
                .openForStartup(oldBundled) { error("old bundled reference already exists") }
            assertEquals(7, ReferenceStore(root, storage, verifier).snapshot().highestActivatedSequence)

            val newBundledBytes = "bundled-v10".toByteArray()
            val newBundled = version(newBundledBytes, 0, "bundled-v10", schemaVersion = "10")
            val upgradedStore = ReferenceStore(root, storage, verifier)
            val selected = upgradedStore.openForStartup(newBundled) { it.writeBytes(newBundledBytes) }

            assertEquals(newBundled, selected.version)
            assertTrue(selected.recoveryReason!!.contains("schema"))
            assertEquals(newBundled, upgradedStore.snapshot().active)
            assertNull(upgradedStore.snapshot().previous)
            assertEquals(7, upgradedStore.snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun corruptStateFallsBackToBundledAndReportsRecovery() {
        val root = Files.createTempDirectory("reference-store-state-recovery").toFile()
        try {
            val storage = MemoryStateStorage().apply { bytes = "corrupt-state".toByteArray() }
            val verifier = FakeDatabaseVerifier()
            val bundledBytes = "bundled".toByteArray()
            val bundled = version(bundledBytes, 0, "bundled")

            val selected = ReferenceStore(root, storage, verifier)
                .openForStartup(bundled) { it.writeBytes(bundledBytes) }

            assertEquals(bundled, selected.version)
            assertTrue(selected.recoveryReason!!.contains("state"))
            assertEquals(1, verifier.calls)
            assertEquals(Long.MAX_VALUE, ReferenceStore(root, storage, verifier).snapshot().highestActivatedSequence)
        } finally {
            root.deleteRecursively()
        }
    }
}
