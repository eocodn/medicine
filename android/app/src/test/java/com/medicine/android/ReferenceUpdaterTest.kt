package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest

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
    ) : ReferenceReleaseSource {
        val downloads = mutableListOf<ReferenceReleaseArtifact>()
        override fun fetchLatest(): VerifiedReferenceRelease = release
        override fun download(
            artifact: ReferenceReleaseArtifact,
            target: File,
            progress: (Long, Long) -> Unit,
        ) {
            downloads += artifact
            target.writeBytes(artifactBytes)
            progress(artifactBytes.size.toLong(), artifactBytes.size.toLong())
            if (failDownload) error("network interrupted")
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

    private fun version(bytes: ByteArray, sequence: Long, label: String) = ReferenceVersion(
        datasetId = dataset(label),
        sha256 = sha(bytes),
        sizeBytes = bytes.size.toLong(),
        schemaVersion = "8",
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
            key = "reference/v1/full/${sha(targetBytes)}.sqlite.gz",
            sha256 = sha("full-$label".toByteArray()),
            sizeBytes = 100,
            kind = ReferenceArtifactKind.FULL_GZIP,
        )
        val patches = if (includeMatchingPatch) listOf(
            ReferenceReleaseArtifact(
                key = "reference/v1/patch/${current.sha256}-${sha(targetBytes)}.mpatch",
                sha256 = sha("patch-$label".toByteArray()),
                sizeBytes = 20,
                kind = ReferenceArtifactKind.CHUNK_PATCH,
                fromSha256 = current.sha256,
                fromSizeBytes = current.sizeBytes,
            )
        ) else emptyList()
        return VerifiedReferenceRelease(
            releaseSequence = sequence,
            datasetId = dataset(label),
            schemaVersion = "8",
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
                .openForStartup("8")!!
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
    fun releaseContractRejectsArtifactKeysWhichDoNotMatchSignedTargetIdentity() {
        val targetBytes = "target-contract".toByteArray()
        val targetSha = sha(targetBytes)
        val currentBytes = "current-contract".toByteArray()
        val current = version(currentBytes, 0, "current-contract")
        val wrongFull = ReferenceReleaseArtifact(
            key = "reference/v1/full/${sha("other".toByteArray())}.sqlite.gz",
            sha256 = sha("full-contract".toByteArray()),
            sizeBytes = 10,
            kind = ReferenceArtifactKind.FULL_GZIP,
        )
        val wrongPatch = ReferenceReleaseArtifact(
            key = "reference/v1/patch/${current.sha256}-${sha("other-target".toByteArray())}.mpatch",
            sha256 = sha("patch-contract".toByteArray()),
            sizeBytes = 5,
            kind = ReferenceArtifactKind.CHUNK_PATCH,
            fromSha256 = current.sha256,
            fromSizeBytes = current.sizeBytes,
        )

        assertTrue(runCatching {
            VerifiedReferenceRelease(
                releaseSequence = 1,
                datasetId = dataset("contract"),
                schemaVersion = "8",
                targetSha256 = targetSha,
                targetSizeBytes = targetBytes.size.toLong(),
                full = wrongFull,
                patches = emptyList(),
            )
        }.exceptionOrNull() is IllegalArgumentException)

        val validFull = wrongFull.copy(key = "reference/v1/full/$targetSha.sqlite.gz")
        assertTrue(runCatching {
            VerifiedReferenceRelease(
                releaseSequence = 1,
                datasetId = dataset("contract"),
                schemaVersion = "8",
                targetSha256 = targetSha,
                targetSizeBytes = targetBytes.size.toLong(),
                full = validFull,
                patches = listOf(wrongPatch),
            )
        }.exceptionOrNull() is IllegalArgumentException)
    }

}
