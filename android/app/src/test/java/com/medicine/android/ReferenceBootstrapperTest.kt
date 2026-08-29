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
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

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
        override fun verifyRuntimeCapabilities(file: File, version: ReferenceVersion) = Unit
    }

    private class FakeSource(
        private val release: VerifiedReferenceRelease,
        failDownloads: Int = 0,
        private val fetchEntered: CountDownLatch? = null,
        private val downloadEntered: CountDownLatch? = null,
        private val continueDownload: CountDownLatch? = null,
    ) : ReferenceReleaseSource {
        private var remainingFailures = failDownloads
        var fetches = 0
        val downloads = mutableListOf<ReferenceReleaseArtifact>()
        val resumeOffsets = mutableListOf<Long>()

        override fun fetchLatest(): VerifiedReferenceRelease {
            fetches += 1
            fetchEntered?.countDown()
            return release
        }

        override fun download(
            artifact: ReferenceReleaseArtifact,
            target: File,
            progress: (Long, Long) -> Unit,
        ) {
            downloads += artifact
            resumeOffsets += target.takeIf { it.isFile }?.length() ?: 0L
            downloadEntered?.countDown()
            continueDownload?.let { latch ->
                check(latch.await(5, TimeUnit.SECONDS)) { "timed out waiting to continue fake download" }
            }
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

    private class RetiredSource(
        private val sequence: Long,
        private val hash: String,
    ) : ReferenceReleaseSource {
        var downloads = 0

        override fun fetchLatest(): VerifiedReferenceRelease {
            throw ReferenceContractRetiredException(
                releaseSequence = sequence,
                rootHash = hash,
                currentContractMajor = 2,
                minimumSupportedContractMajor = 2,
            )
        }

        override fun download(
            artifact: ReferenceReleaseArtifact,
            target: File,
            progress: (Long, Long) -> Unit,
        ) {
            downloads += 1
            error("retired contract must not download")
        }
    }

    private class FakeRebuilder : ReferenceArtifactRebuilder {
        var currentWasNull = false
        override fun rebuild(
            current: InstalledReferenceVersion?,
            target: ReferenceVersion,
            artifact: ReferenceReleaseArtifact,
            downloaded: File,
            output: File,
            observer: ReferenceUpdateObserver,
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

    private fun bootstrapper(
        root: File,
        store: ReferenceStore,
        source: ReferenceReleaseSource,
        rebuilder: ReferenceArtifactRebuilder,
        storageCapacity: ReferenceStorageCapacity,
        observer: ReferenceUpdateObserver? = null,
    ) = if (observer == null) {
        ReferenceBootstrapper(
            root,
            store,
            source,
            rebuilder,
            storageCapacity,
            planner = TestReferenceLifecyclePlanner,
        )
    } else {
        ReferenceBootstrapper(
            root,
            store,
            source,
            rebuilder,
            storageCapacity,
            observer,
            TestReferenceLifecyclePlanner,
        )
    }

    private fun release(contractMajor: Int = 1, sequence: Long = 12): VerifiedReferenceRelease =
        VerifiedReferenceRelease(
            releaseSequence = sequence,
            rootHash = sha("root-$sequence".toByteArray()),
            datasetId = "sha256:" + sha("dataset-$sequence".toByteArray()),
            contractMajor = contractMajor,
            targetSha256 = sha(TARGET_BYTES),
            targetSizeBytes = TARGET_BYTES.size.toLong(),
            full = ReferenceReleaseArtifact(
                contractMajor = contractMajor,
                key = "reference/v2/contracts/$contractMajor/full/${sha(TARGET_BYTES)}.sqlite.gz",
                sha256 = sha(FULL_BYTES),
                sizeBytes = FULL_BYTES.size.toLong(),
                kind = ReferenceArtifactKind.FULL_GZIP,
            ),
            patches = emptyList(),
        )

    @Test
    fun androidInstallerKeepsCurrentMainStateFilenameForInPlaceMigration() {
        assertEquals("state.v1", REFERENCE_STATE_FILE)
    }

    @Test
    fun firstLaunchDownloadsFullReleaseAndInstallsItAsActive() {
        val root = Files.createTempDirectory("reference-bootstrap").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val source = FakeSource(release())
            val rebuilder = FakeRebuilder()
            val observer = RecordingObserver()
            val bootstrapper = bootstrapper(
                root,
                store,
                source,
                rebuilder,
                FixedStorageCapacity(Long.MAX_VALUE),
                observer,
            )

            val installed = bootstrapper.ensureInstalled(1)

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
    fun firstLaunchPreparationExposesSignedDownloadSizeWithoutDownloading() {
        val root = Files.createTempDirectory("reference-bootstrap-prepare").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val source = FakeSource(release())
            val bootstrapper = bootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            )

            val preparation = bootstrapper.prepare(1)

            assertTrue(preparation is ReferenceBootstrapPreparation.Download)
            preparation as ReferenceBootstrapPreparation.Download
            assertEquals(FULL_BYTES.size.toLong(), preparation.downloadSizeBytes)
            assertEquals(FULL_BYTES.size.toLong(), preparation.totalDownloadBytes)
            assertTrue(source.downloads.isEmpty())
            assertNull(store.snapshot().active)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun preparedBootstrapInstallsWithoutFetchingManifestAgain() {
        val root = Files.createTempDirectory("reference-bootstrap-prepared-install").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val source = FakeSource(release())
            val bootstrapper = bootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            )

            val preparation = bootstrapper.prepare(1)
            val installed = bootstrapper.installPrepared(preparation, 1)

            assertEquals(1, source.fetches)
            assertEquals(1, source.downloads.size)
            assertEquals(12, installed.version.releaseSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun retiredContractWithoutLkgReturnsUnavailableModeAndPersistsRetirement() {
        val root = Files.createTempDirectory("reference-bootstrap-retired-empty").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val source = RetiredSource(33, "f".repeat(64))
            val bootstrapper = bootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            )

            val selected = bootstrapper.ensureInstalledOrRetired(1)

            assertNull(selected)
            assertEquals(0, source.downloads)
            assertTrue(store.isContractRetired(1))
            assertEquals(33, store.snapshot().highestSeenRootSequence)
            assertEquals("f".repeat(64), store.snapshot().highestSeenRootHash)

            val reopened = bootstrapper(
                root,
                ReferenceStore(root, storage, FakeDatabaseVerifier()),
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            ).ensureInstalledOrRetired(1)
            assertNull(reopened)
            assertEquals(0, source.downloads)
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
                currentRelease.contractMajor,
                currentRelease.releaseSequence,
            )
            store.installInitial(
                version,
                File(root, ".initial.sqlite").apply { writeBytes(TARGET_BYTES) },
            )
            val source = FakeSource(release(sequence = 10))

            val installed = bootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            ).ensureInstalled(1)

            assertEquals(9, installed.version.releaseSequence)
            assertEquals(0, source.fetches)
            assertTrue(source.downloads.isEmpty())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun incompatibleLatestContractIsRejectedBeforeArtifactDownload() {
        val root = Files.createTempDirectory("reference-bootstrap-contract").toFile()
        try {
            val source = FakeSource(release(contractMajor = 2))
            val error = runCatching {
                bootstrapper(
                    root,
                    ReferenceStore(root, MemoryStateStorage(), FakeDatabaseVerifier()),
                    source,
                    FakeRebuilder(),
                    FixedStorageCapacity(Long.MAX_VALUE),
                ).ensureInstalled(1)
            }.exceptionOrNull()

            assertNotNull(error)
            assertTrue(error!!.message!!.contains("contract"))
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
                bootstrapper(
                    root,
                    ReferenceStore(root, MemoryStateStorage(), FakeDatabaseVerifier()),
                    source,
                    FakeRebuilder(),
                    FixedStorageCapacity(0),
                ).ensureInstalled(1)
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
                bootstrapper(
                    root,
                    store,
                    source,
                    FakeRebuilder(),
                    FixedStorageCapacity(Long.MAX_VALUE),
                ).ensureInstalled(1)
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
            val bootstrapper = bootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            )

            assertNotNull(runCatching { bootstrapper.ensureInstalled(1) }.exceptionOrNull())
            val installed = bootstrapper.ensureInstalled(1)

            assertEquals(listOf(0L, (FULL_BYTES.size / 2).coerceAtLeast(1).toLong()), source.resumeOffsets)
            assertEquals(12, installed.version.releaseSequence)
            assertEquals(installed.version, store.snapshot().active)
            assertFalse(root.listFiles().orEmpty().any { it.name.startsWith(".bootstrap-artifact-") })
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun separateBootstrapInstancesSerializeSharedReferenceIo() {
        val root = Files.createTempDirectory("reference-bootstrap-concurrent").toFile()
        val executor = Executors.newFixedThreadPool(2)
        val firstDownloadEntered = CountDownLatch(1)
        val continueFirstDownload = CountDownLatch(1)
        val secondFetchEntered = CountDownLatch(1)
        try {
            val stateStorage = MemoryStateStorage()
            val firstSource = FakeSource(
                release(),
                downloadEntered = firstDownloadEntered,
                continueDownload = continueFirstDownload,
            )
            val secondSource = FakeSource(release(), fetchEntered = secondFetchEntered)
            val first = bootstrapper(
                root,
                ReferenceStore(root, stateStorage, FakeDatabaseVerifier()),
                firstSource,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            )
            val second = bootstrapper(
                root,
                ReferenceStore(root, stateStorage, FakeDatabaseVerifier()),
                secondSource,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            )

            val firstFuture = executor.submit<InstalledReferenceVersion> { first.ensureInstalled(1) }
            assertTrue(firstDownloadEntered.await(2, TimeUnit.SECONDS))
            val secondFuture = executor.submit<InstalledReferenceVersion> { second.ensureInstalled(1) }

            assertFalse(secondFetchEntered.await(200, TimeUnit.MILLISECONDS))
            continueFirstDownload.countDown()
            assertEquals(12, firstFuture.get(2, TimeUnit.SECONDS).version.releaseSequence)
            assertEquals(12, secondFuture.get(2, TimeUnit.SECONDS).version.releaseSequence)
            assertEquals(0, secondSource.fetches)
        } finally {
            continueFirstDownload.countDown()
            executor.shutdownNow()
            root.deleteRecursively()
        }
    }

    @Test
    fun bootstrapAdoptsVerifiedTargetLeftBeforeStateCommit() {
        val root = Files.createTempDirectory("reference-bootstrap-adopt-e2e").toFile()
        try {
            val stateStorage = MemoryStateStorage()
            val currentRelease = release()
            val version = ReferenceVersion(
                currentRelease.datasetId,
                currentRelease.targetSha256,
                currentRelease.targetSizeBytes,
                currentRelease.contractMajor,
                currentRelease.releaseSequence,
            )
            val store = ReferenceStore(root, stateStorage, FakeDatabaseVerifier())
            store.fileFor(version).writeBytes(TARGET_BYTES)
            val source = FakeSource(currentRelease)

            val installed = bootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            ).ensureInstalled(1)

            assertEquals(version, installed.version)
            assertTrue(source.downloads.isEmpty())
            assertEquals(version, store.snapshot().active)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun newerLatestReclaimsStaleBootstrapCheckpoint() {
        val root = Files.createTempDirectory("reference-bootstrap-stale-release").toFile()
        try {
            val stale = File(
                root,
                ".bootstrap-artifact-11-${sha("old-artifact".toByteArray())}.part",
            ).apply { writeBytes("stale".toByteArray()) }

            bootstrapper(
                root,
                ReferenceStore(root, MemoryStateStorage(), FakeDatabaseVerifier()),
                FakeSource(release(sequence = 12)),
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            ).ensureInstalled(1)

            assertFalse(stale.exists())
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun existingLkgReclaimsOrphanedBootstrapFilesWithoutNetwork() {
        val root = Files.createTempDirectory("reference-bootstrap-orphan-cleanup").toFile()
        try {
            val stateStorage = MemoryStateStorage()
            val store = ReferenceStore(root, stateStorage, FakeDatabaseVerifier())
            val currentRelease = release(sequence = 9)
            val version = ReferenceVersion(
                currentRelease.datasetId,
                currentRelease.targetSha256,
                currentRelease.targetSizeBytes,
                currentRelease.contractMajor,
                currentRelease.releaseSequence,
            )
            store.installInitial(
                version,
                File(root, ".initial.sqlite").apply { writeBytes(TARGET_BYTES) },
            )
            val artifactOrphan = File(root, ".bootstrap-artifact-9-${sha(FULL_BYTES)}.part")
                .apply { writeBytes(FULL_BYTES) }
            val candidateOrphan = File(root, ".bootstrap-candidate-9-${sha(TARGET_BYTES)}.sqlite")
                .apply { writeBytes(TARGET_BYTES) }
            val source = FakeSource(release(sequence = 10))

            bootstrapper(
                root,
                store,
                source,
                FakeRebuilder(),
                FixedStorageCapacity(Long.MAX_VALUE),
            ).ensureInstalled(1)

            assertFalse(artifactOrphan.exists())
            assertFalse(candidateOrphan.exists())
            assertEquals(0, source.fetches)
        } finally {
            root.deleteRecursively()
        }
    }

    companion object {
        private val TARGET_BYTES = "verified-reference-target".toByteArray()
        private val FULL_BYTES = "compressed-full".toByteArray()
    }
}
