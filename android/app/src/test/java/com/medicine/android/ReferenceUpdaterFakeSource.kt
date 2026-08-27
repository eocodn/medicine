package com.medicine.android

import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

internal class FakeSource(
    private val release: VerifiedReferenceRelease,
    private val artifactBytes: ByteArray = "artifact".toByteArray(),
    private val failDownload: Boolean = false,
    private val failDownloadKinds: Set<ReferenceArtifactKind> = emptySet(),
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
        if (failDownload || artifact.kind in failDownloadKinds) error("network interrupted")
    }
}