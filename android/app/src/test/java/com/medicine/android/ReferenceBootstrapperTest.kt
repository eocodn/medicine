package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest

class ReferenceBootstrapperTest {
    private class MemoryStateStorage : ReferenceStateStorage {
        var bytes: ByteArray? = null
        override fun read(): ByteArray? = bytes?.copyOf()
        override fun write(value: ByteArray) { bytes = value.copyOf() }
    }

    private class FakeDatabaseVerifier : ReferenceDatabaseVerifier {
        override fun verify(file: File, version: ReferenceVersion) {
            require(file.readBytes().contentEquals(TARGET_BYTES)) { "unexpected target bytes" }
        }
    }

    private class FakeSource(
        private val release: VerifiedReferenceRelease,
        failDownloads: Int = 0,
    ) : ReferenceReleaseSource {
        private var remainingFailures = failDownloads
        var fetches = 0
        val downloads = mutableListOf<ReferenceReleaseArtifact>()
        val resumeOffsets = mutableListOf<Long>()

        override fun fetchLatest(): VerifiedReferenceRelease {
            fetches += 1
            return release
        }

        override fun download(
            artifact: ReferenceReleaseArtifact,
            target: File,
            progress: (Long, Long) -> Unit,
        ) {
            downloads += artifact
            resumeOffsets += target.takeIf { it.isFile }?.length() ?: 0L
            if (remainingFailures > 0) {
                remainingFailures -= 1
                val partialSize = (FULL_BYTES.size / 2).coerceAtLeast(1)
                target.writeBytes(FULL_BYTES.copyOf(partialSize))
                progress(partialSize.toLong(), FULL_BYTES.size.toLong())
                error("network interrupted")
            }
            target.writeBytes(FULL_BYTES)
            progress(FULL_BYTES.size.toLong(), FULL_BYTES.size.toLong())
        }
    }

    private class FakeRebuilder : ReferenceArtifactRebuilder {
        var currentWasNull = false
        override fun rebuild(
            current: InstalledReferenceVersion?,
            artifact: ReferenceReleaseArtifact,
            downloaded: File,
            output: File,
        ) {
            currentWasNull = current == null
            assertEquals(ReferenceArtifactKind.FULL_GZIP, artifact.kind)
            output.writeBytes(TARGET_BYTES)
        }
    }

    private class FixedStorageCapacity(private val available: Long) : ReferenceStorageCapacity {
        override fun availableBytes(path: File): Long = available
    }

    private class RecordingObserver : ReferenceUpdateObserver {
        val phases = mutableListOf<String>()
        val progress = mutableListOf<Pair<Long, Long>>()
        override fun phase(name: String) { phases += name }
        override fun progress(name: String, completedBytes: Long, totalBytes: Long) {
            if (name == "download") progress += completedBytes to totalBytes
        }
    }

    private fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }

    private fun release(schemaVersion: String = "10", sequence: Long = 12): VerifiedReferenceRelease =
        VerifiedReferenceRelease(
            releaseSequence = sequence,
            datasetId = "sha256:" + sha("dataset-$sequence".toByteArray()),
            schemaVersion = schemaVersion,
            targetSha256 = sha(TARGET_BYTES),
            targetSizeBytes = TARGET_BYTES.size.toLong(),
            full = ReferenceReleaseArtifact(
                key = "reference/v1/full/${sha(TARGET_BYTES)}.sqlite.gz",
                sha256 = sha(FULL_BYTES),
                sizeBytes = FULL_BYTES.size.toLong(),
                kind = ReferenceArtifactKind.FULL_GZIP,
            ),
            patches = emptyList(),
        )

    @Test
    fun firstLaunchDownloadsFullReleaseAndInstallsItAsActive() {
        val root = Files.createTempDirectory("reference-bootstrap").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val source = FakeSource(release())
            val rebuilder = FakeRebuilder()
            val observer = RecordingObserver()
            val bootstrapper = ReferenceBootstrapper(
                root,
                store,
                source,
                rebuilder,
                FixedStorageCapacity(Long.MAX_VALUE),
                observer,
            )

            val installed = bootstrapper.ensureInstalled("10")

            assertEquals(12, installed.version.releaseSequence)
            assertEquals(ReferenceArtifactKind.FULL_GZIP, source.downloads.single().kind)
            assertTrue(rebuilder.currentWasNull)
            assertEquals(installed.version, store.snapshot().active)
            assertEquals(12, store.snapshot().highestActivatedSequence)
            assertNull(store.snapshot().pending)
            assertTrue(installed.file.isFile)
            assertTrue(observer.phases.containsAll(listOf("manifest", "full-download", "rebuild", "verify-and-install", "ready")))
            assertEquals(FULL_BYTES.size.toLong() to FULL_BYTES.size.toLong(), observer.progress.last())
            assertFalse(root.listFiles().orEmpty().any { it.name.startsWith(".bootstrap-candidate-") })
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun existingLkgStartsWithoutNetwork() {
        val root = Files.createTempDirectory("reference-bootstrap-lkg").toFile()
        try {
            val stateStorage = MemoryStateStorage()
            val store = ReferenceStore(root, stateStorage, FakeDatabaseVerifier())
            val currentRelease = release(sequence = 9)
            val version = ReferenceVersion(
                currentRelease.datasetId,
                currentRelease.targetSha256,
                currentRelease.targetSizeBytes,
                currentRelease.schemaVersion,
                currentRelease.releaseSequence,
            )
            store.installInitial(
                version,
                File(root, ".initial.sqlite").apply { writeBytes(TARGET_BYTES) },
            )
            val source = FakeSource(release(sequence = 10))

            val installed = ReferenceBootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            ).ensureInstalled("10")

            assertEquals(9, installed.version.releaseSequence)
            assertEquals(0, source.fetches)
            assertTrue(source.downloads.isEmpty())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun incompatibleLatestSchemaIsRejectedBeforeArtifactDownload() {
        val root = Files.createTempDirectory("reference-bootstrap-schema").toFile()
        try {
            val source = FakeSource(release(schemaVersion = "11"))
            val error = runCatching {
                ReferenceBootstrapper(
                    root,
                    ReferenceStore(root, MemoryStateStorage(), FakeDatabaseVerifier()),
                    source,
                    FakeRebuilder(),
                    FixedStorageCapacity(Long.MAX_VALUE),
                ).ensureInstalled("10")
            }.exceptionOrNull()

            assertNotNull(error)
            assertTrue(error!!.message!!.contains("schema"))
            assertTrue(source.downloads.isEmpty())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun insufficientStorageFailsBeforeArtifactDownload() {
        val root = Files.createTempDirectory("reference-bootstrap-storage").toFile()
        try {
            val source = FakeSource(release())
            val error = runCatching {
                ReferenceBootstrapper(
                    root,
                    ReferenceStore(root, MemoryStateStorage(), FakeDatabaseVerifier()),
                    source,
                    FakeRebuilder(),
                    FixedStorageCapacity(0),
                ).ensureInstalled("10")
            }.exceptionOrNull()

            assertTrue(error is ReferenceBootstrapStorageException)
            assertTrue(source.downloads.isEmpty())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun interruptedDownloadKeepsCheckpointAndDoesNotMutateReferenceState() {
        val root = Files.createTempDirectory("reference-bootstrap-resume").toFile()
        try {
            val stateStorage = MemoryStateStorage()
            val store = ReferenceStore(root, stateStorage, FakeDatabaseVerifier())
            val source = FakeSource(release(), failDownloads = 1)

            val error = runCatching {
                ReferenceBootstrapper(
                    root,
                    store,
                    source,
                    FakeRebuilder(),
                    FixedStorageCapacity(Long.MAX_VALUE),
                ).ensureInstalled("10")
            }.exceptionOrNull()

            assertNotNull(error)
            assertNull(store.snapshot().active)
            assertEquals(0, store.snapshot().highestActivatedSequence)
            assertTrue(root.listFiles().orEmpty().any {
                it.name.startsWith(".bootstrap-artifact-") && it.name.endsWith(".part")
            })
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun retryReusesPartialDownloadCheckpointAndCompletesInitialInstall() {
        val root = Files.createTempDirectory("reference-bootstrap-retry").toFile()
        try {
            val stateStorage = MemoryStateStorage()
            val store = ReferenceStore(root, stateStorage, FakeDatabaseVerifier())
            val source = FakeSource(release(), failDownloads = 1)
            val bootstrapper = ReferenceBootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            )

            assertNotNull(runCatching { bootstrapper.ensureInstalled("10") }.exceptionOrNull())
            val installed = bootstrapper.ensureInstalled("10")

            assertEquals(listOf(0L, (FULL_BYTES.size / 2).coerceAtLeast(1).toLong()), source.resumeOffsets)
            assertEquals(12, installed.version.releaseSequence)
            assertEquals(installed.version, store.snapshot().active)
            assertFalse(root.listFiles().orEmpty().any { it.name.startsWith(".bootstrap-artifact-") })
        } finally {
            root.deleteRecursively()
        }
    }

    companion object {
        private val TARGET_BYTES = "verified-reference-target".toByteArray()
        private val FULL_BYTES = "compressed-full".toByteArray()
    }
}