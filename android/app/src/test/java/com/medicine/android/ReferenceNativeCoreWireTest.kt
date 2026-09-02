package com.medicine.android

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReferenceNativeCoreWireTest {
    @Test
    fun bootstrapPlannerReleaseUsesJsonArrayForPatches() {
        val full = ReferenceReleaseArtifact(
            contractMajor = 1,
            key = "reference/v2/contracts/1/full/${hash('d')}.sqlite.gz",
            sha256 = hash('b'),
            sizeBytes = 123,
            kind = ReferenceArtifactKind.FULL_GZIP,
        )
        val patch = ReferenceReleaseArtifact(
            contractMajor = 1,
            key = "reference/v2/contracts/1/patch/${hash('c')}-${hash('d')}.mpatch",
            sha256 = hash('e'),
            sizeBytes = 45,
            kind = ReferenceArtifactKind.CHUNK_PATCH,
            fromSha256 = hash('c'),
            fromSizeBytes = 100,
        )
        val release = VerifiedReferenceRelease(
            releaseSequence = 36,
            rootHash = hash('f'),
            datasetId = "sha256:${hash('1')}",
            contractMajor = 1,
            targetSha256 = hash('d'),
            targetSizeBytes = 223_334_400,
            full = full,
            patches = listOf(patch),
        )

        val encoded = ReferencePlannerWire.releaseJson(release).toString()
        val decoded = JSONObject(encoded)
        val patches = decoded.getJSONArray("patches")

        assertEquals(1, patches.length())
        assertEquals(patch.key, patches.getJSONObject(0).getString("key"))
        assertEquals("chunk_patch", patches.getJSONObject(0).getString("kind"))
        assertEquals(36, decoded.getLong("release_sequence"))
        assertTrue(encoded.contains("\"patches\":["))
    }

    @Test
    fun bootstrapPlannerReleaseUsesEmptyJsonArrayWhenNoPatchesExist() {
        val full = ReferenceReleaseArtifact(
            contractMajor = 1,
            key = "reference/v2/contracts/1/full/${hash('d')}.sqlite.gz",
            sha256 = hash('b'),
            sizeBytes = 123,
            kind = ReferenceArtifactKind.FULL_GZIP,
        )
        val release = VerifiedReferenceRelease(
            releaseSequence = 36,
            rootHash = hash('f'),
            datasetId = "sha256:${hash('1')}",
            contractMajor = 1,
            targetSha256 = hash('d'),
            targetSizeBytes = 223_334_400,
            full = full,
            patches = emptyList(),
        )

        val decoded = JSONObject(ReferencePlannerWire.releaseJson(release).toString())

        assertEquals(0, decoded.getJSONArray("patches").length())
    }

    private fun hash(character: Char): String = character.toString().repeat(64)
}