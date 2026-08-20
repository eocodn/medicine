package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class ReferenceUpdaterTest {
    private class MemoryStateStorage : ReferenceStateStorage {
        var bytes: ByteArray? = null
        override fun read(): ByteArray? = bytes?.copyOf()
        override fun write(value: ByteArray) { bytes = value.copyOf() }
    }

    private class FakeDatabaseVerifier : ReferenceDatabaseVerifier {
        override fun verify(file: File, version: ReferenceVersion) = Unit
    }

    private class FakeSource(
        private val release: VerifiedReferenceRelease,
        private val artifactBytes: ByteArray = "artifact".toByteArray(),
        private val failDownload: Boolean = false,
        private val fetchEntered: CountDownLatch? = null,
        private val downloadEntered: CountDownLatch? = null,
        private val continueDownload: CountDownLatch? = null,
    ) : ReferenceReleaseSource {
        var fetches = 0
        val downloads = mutableListOf<ReferenceReleaseArtifact>()
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
            downloadEntered?.countDown()
            continueDownload?.let { latch ->
                check(latch.await(5, TimeUnit.SECONDS)) { "timed out waiting to continue fake download" }
            }
            target.writeBytes(artifactBytes)
            progress(artifactBytes.size.toLong(), artifactBytes.size.toLong())
            if (failDownload) error("network interrupted")
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
            error("retired contract must not download artifacts")
        }
    }

    private class FakeRebuilder(private val targetBytes: ByteArray) : ReferenceArtifactRebuilder {
        var usedArtifact: ReferenceReleaseArtifact? = null
        override fun rebuild(
            current: InstalledReferenceVersion?,
            artifact: ReferenceReleaseArtifact,
            downloaded: File,
            output: File,
        ) {
            usedArtifact = artifact
            output.writeBytes(targetBytes)
        }
    }

    private fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }

    private fun dataset(label: String): String = "sha256:" + sha(label.toByteArray())

    private fun rootHash(sequence: Long): String = sha("root-$sequence".toByteArray())

    private fun version(bytes: ByteArray, sequence: Long, label: String) = ReferenceVersion(
        datasetId = dataset(label),
        sha256 = sha(bytes),
        sizeBytes = bytes.size.toLong(),
        contractMajor = 1,
        releaseSequence = sequence,
    )

    private fun release(
        targetBytes: ByteArray,
        sequence: Long,
        label: String,
        current: ReferenceVersion,
        includeMatchingPatch: Boolean,
    ): VerifiedReferenceRelease {
        val full = ReferenceReleaseArtifact(
            contractMajor = 1,
            key = "reference/v2/contracts/1/full/${sha(targetBytes)}.sqlite.gz",
            sha256 = sha("full-$label".toByteArray()),
            sizeBytes = 100,
            kind = ReferenceArtifactKind.FULL_GZIP,
        )
        val patches = if (includeMatchingPatch) listOf(
            ReferenceReleaseArtifact(
                contractMajor = 1,
                key = "reference/v2/contracts/1/patch/${current.sha256}-${sha(targetBytes)}.mpatch",
                sha256 = sha("patch-$label".toByteArray()),
                sizeBytes = 20,
                kind = ReferenceArtifactKind.CHUNK_PATCH,
                fromSha256 = current.sha256,
                fromSizeBytes = current.sizeBytes,
            )
        ) else emptyList()
        return VerifiedReferenceRelease(
            releaseSequence = sequence,
            rootHash = rootHash(sequence),
            datasetId = dataset(label),
            contractMajor = 1,
            targetSha256 = sha(targetBytes),
            targetSizeBytes = targetBytes.size.toLong(),
            full = full,
            patches = patches,
        )
    }

    @Test
    fun updaterPrefersDirectPatchAndStagesWithoutHotSwap() {
        val root = Files.createTempDirectory("reference-updater-patch").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val currentBytes = "current".toByteArray()
            val current = version(currentBytes, 1, "current")
            val installed = store.installInitial(
                current,
                File(root, ".current.sqlite").apply { writeBytes(currentBytes) },
            )
            val targetBytes = "target-one".toByteArray()
            val release = release(targetBytes, 2, "one", current, includeMatchingPatch = true)
            val source = FakeSource(release)
            val rebuilder = FakeRebuilder(targetBytes)

            val result = ReferenceUpdater(root, store, source, rebuilder).checkForUpdate(installed)

            assertEquals(ReferenceUpdateStatus.STAGED, result.status)
            assertEquals(ReferenceArtifactKind.CHUNK_PATCH, source.downloads.single().kind)
            assertEquals(ReferenceArtifactKind.CHUNK_PATCH, rebuilder.usedArtifact!!.kind)
            assertEquals(current, store.snapshot().active)
            assertEquals(2, store.snapshot().pending!!.releaseSequence)
            assertEquals(current, installed.version)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun updaterFallsBackToFullWhenNoPatchMatchesCurrentBytes() {
        val root = Files.createTempDirectory("reference-updater-full").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val currentBytes = "current".toByteArray()
            val current = version(currentBytes, 1, "current")
            val installed = store.installInitial(
                current,
                File(root, ".current.sqlite").apply { writeBytes(currentBytes) },
            )
            val targetBytes = "target-two".toByteArray()
            val release = release(targetBytes, 2, "two", current, includeMatchingPatch = false)
            val source = FakeSource(release)
            val rebuilder = FakeRebuilder(targetBytes)

            val result = ReferenceUpdater(root, store, source, rebuilder).checkForUpdate(installed)

            assertEquals(ReferenceUpdateStatus.STAGED, result.status)
            assertEquals(ReferenceArtifactKind.FULL_GZIP, source.downloads.single().kind)
            assertEquals(2, store.snapshot().pending!!.releaseSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun signedContractRetirementReturnsUpdateRequiredBeforeAnyArtifactDownload() {
        val root = Files.createTempDirectory("reference-updater-retired").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val currentBytes = "current-retired".toByteArray()
            val current = version(currentBytes, 7, "current-retired")
            val installed = store.installInitial(
                current,
                File(root, ".current-retired.sqlite").apply { writeBytes(currentBytes) },
            )
            val signedRootHash = rootHash(8)
            val source = RetiredSource(8, signedRootHash)

            val result = ReferenceUpdater(
                root,
                store,
                source,
                FakeRebuilder("unused".toByteArray()),
            ).checkForUpdate(installed)

            assertEquals(ReferenceUpdateStatus.UPDATE_REQUIRED, result.status)
            assertEquals(0, source.downloads)
            assertEquals(current, store.snapshot().active)
            assertEquals(8, store.snapshot().highestSeenRootSequence)
            assertEquals(signedRootHash, store.snapshot().highestSeenRootHash)
            assertEquals(1, store.snapshot().highestRetiredContractMajor)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun newerRootWithUnchangedContractTargetIsUpToDateWithoutDownloadOrRestage() {
        val root = Files.createTempDirectory("reference-updater-root-only").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val currentBytes = "current-root-only".toByteArray()
            val current = version(currentBytes, 7, "current-root-only")
            val installed = store.installInitial(
                current,
                File(root, ".current-root-only.sqlite").apply { writeBytes(currentBytes) },
            )
            val release = release(
                currentBytes,
                sequence = 8,
                label = "current-root-only",
                current = current,
                includeMatchingPatch = false,
            )
            val source = FakeSource(release)

            val result = ReferenceUpdater(
                root,
                store,
                source,
                FakeRebuilder(currentBytes),
            ).checkForUpdate(installed)

            assertEquals(ReferenceUpdateStatus.UP_TO_DATE, result.status)
            assertTrue(source.downloads.isEmpty())
            assertEquals(current, store.snapshot().active)
            assertNull(store.snapshot().pending)
            assertEquals(8, store.snapshot().highestSeenRootSequence)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun updaterRejectsReleaseBelowActivatedHighWaterBeforeDownloading() {
        val root = Files.createTempDirectory("reference-updater-rollback").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val bundledBytes = "initial".toByteArray()
            val bundled = version(bundledBytes, 1, "initial")
            store.installInitial(
                bundled,
                File(root, ".initial.sqlite").apply { writeBytes(bundledBytes) },
            )
            val sevenBytes = "release-seven".toByteArray()
            val seven = version(sevenBytes, 7, "seven")
            store.stagePending(seven, File(root, ".seven.sqlite").apply { writeBytes(sevenBytes) })
            val installed = ReferenceStore(root, storage, FakeDatabaseVerifier())
                .openForStartup(1)!!
            val oldBytes = "release-six".toByteArray()
            val oldRelease = release(oldBytes, 6, "six", installed.version, includeMatchingPatch = false)
            val source = FakeSource(oldRelease)

            val result = ReferenceUpdater(root, ReferenceStore(root, storage, FakeDatabaseVerifier()), source, FakeRebuilder(oldBytes))
                .checkForUpdate(installed)

            assertEquals(ReferenceUpdateStatus.ROLLBACK_REJECTED, result.status)
            assertTrue(source.downloads.isEmpty())
            assertNull(ReferenceStore(root, storage, FakeDatabaseVerifier()).snapshot().pending)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun failedDownloadLeavesActiveStateUnchangedAndReportsFailure() {
        val root = Files.createTempDirectory("reference-updater-failure").toFile()
        try {
            val storage = MemoryStateStorage()
            val store = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val currentBytes = "current".toByteArray()
            val current = version(currentBytes, 1, "current")
            val installed = store.installInitial(
                current,
                File(root, ".current.sqlite").apply { writeBytes(currentBytes) },
            )
            val targetBytes = "target-failure".toByteArray()
            val source = FakeSource(
                release(targetBytes, 3, "failure", current, includeMatchingPatch = false),
                failDownload = true,
            )

            val result = ReferenceUpdater(root, store, source, FakeRebuilder(targetBytes)).checkForUpdate(installed)

            assertEquals(ReferenceUpdateStatus.FAILED, result.status)
            assertTrue(result.detail!!.contains("network interrupted"))
            assertEquals(current, store.snapshot().active)
            assertNull(store.snapshot().pending)
            assertFalse(root.listFiles().orEmpty().any { it.name.startsWith(".candidate-") })
            assertTrue(root.listFiles().orEmpty().any { it.name.startsWith(".artifact-") && it.name.endsWith(".part") })
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun separateUpdaterInstancesSerializeSharedReferenceIo() {
        val root = Files.createTempDirectory("reference-updater-concurrent").toFile()
        val executor = Executors.newFixedThreadPool(2)
        val firstDownloadEntered = CountDownLatch(1)
        val continueFirstDownload = CountDownLatch(1)
        val secondFetchEntered = CountDownLatch(1)
        try {
            val storage = MemoryStateStorage()
            val currentBytes = "current-concurrent".toByteArray()
            val current = version(currentBytes, 1, "current-concurrent")
            val initialStore = ReferenceStore(root, storage, FakeDatabaseVerifier())
            val installed = initialStore.installInitial(
                current,
                File(root, ".current-concurrent.sqlite").apply { writeBytes(currentBytes) },
            )
            val targetBytes = "target-concurrent".toByteArray()
            val nextRelease = release(
                targetBytes,
                2,
                "concurrent",
                current,
                includeMatchingPatch = false,
            )
            val firstSource = FakeSource(
                nextRelease,
                downloadEntered = firstDownloadEntered,
                continueDownload = continueFirstDownload,
            )
            val secondSource = FakeSource(nextRelease, fetchEntered = secondFetchEntered)
            val firstUpdater = ReferenceUpdater(
                root,
                ReferenceStore(root, storage, FakeDatabaseVerifier()),
                firstSource,
                FakeRebuilder(targetBytes),
            )
            val secondUpdater = ReferenceUpdater(
                root,
                ReferenceStore(root, storage, FakeDatabaseVerifier()),
                secondSource,
                FakeRebuilder(targetBytes),
            )

            val firstFuture = executor.submit<ReferenceUpdateResult> { firstUpdater.checkForUpdate(installed) }
            assertTrue(firstDownloadEntered.await(2, TimeUnit.SECONDS))
            val secondFuture = executor.submit<ReferenceUpdateResult> { secondUpdater.checkForUpdate(installed) }

            assertFalse(secondFetchEntered.await(200, TimeUnit.MILLISECONDS))
            continueFirstDownload.countDown()
            assertEquals(ReferenceUpdateStatus.STAGED, firstFuture.get(2, TimeUnit.SECONDS).status)
            assertEquals(ReferenceUpdateStatus.STAGED, secondFuture.get(2, TimeUnit.SECONDS).status)
        } finally {
            continueFirstDownload.countDown()
            executor.shutdownNow()
            root.deleteRecursively()
        }
    }

    @Test
    fun releaseContractRejectsArtifactKeysWhichDoNotMatchSignedTargetIdentity() {
        val targetBytes = "target-contract".toByteArray()
        val targetSha = sha(targetBytes)
        val currentBytes = "current-contract".toByteArray()
        val current = version(currentBytes, 0, "current-contract")
        val wrongFull = ReferenceReleaseArtifact(
            contractMajor = 1,
            key = "reference/v2/contracts/1/full/${sha("other".toByteArray())}.sqlite.gz",
            sha256 = sha("full-contract".toByteArray()),
            sizeBytes = 10,
            kind = ReferenceArtifactKind.FULL_GZIP,
        )
        val wrongPatch = ReferenceReleaseArtifact(
            contractMajor = 1,
            key = "reference/v2/contracts/1/patch/${current.sha256}-${sha("other-target".toByteArray())}.mpatch",
            sha256 = sha("patch-contract".toByteArray()),
            sizeBytes = 5,
            kind = ReferenceArtifactKind.CHUNK_PATCH,
            fromSha256 = current.sha256,
            fromSizeBytes = current.sizeBytes,
        )

        assertTrue(runCatching {
            VerifiedReferenceRelease(
                releaseSequence = 1,
                rootHash = rootHash(1),
                datasetId = dataset("contract"),
                contractMajor = 1,
                targetSha256 = targetSha,
                targetSizeBytes = targetBytes.size.toLong(),
                full = wrongFull,
                patches = emptyList(),
            )
        }.exceptionOrNull() is IllegalArgumentException)

        val validFull = wrongFull.copy(key = "reference/v2/contracts/1/full/$targetSha.sqlite.gz")
        assertTrue(runCatching {
            VerifiedReferenceRelease(
                releaseSequence = 1,
                rootHash = rootHash(1),
                datasetId = dataset("contract"),
                contractMajor = 1,
                targetSha256 = targetSha,
                targetSizeBytes = targetBytes.size.toLong(),
                full = validFull,
                patches = listOf(wrongPatch),
            )
        }.exceptionOrNull() is IllegalArgumentException)
    }

}
