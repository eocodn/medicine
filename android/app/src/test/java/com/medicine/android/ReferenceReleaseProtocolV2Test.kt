package com.medicine.android

import java.security.MessageDigest
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReferenceReleaseProtocolV2Test {
    private val targetSha = "a".repeat(64)
    private val fullSha = "b".repeat(64)
    private val sourceSha = "c".repeat(64)
    private val patchSha = "d".repeat(64)
    private val datasetId = "sha256:" + "e".repeat(64)

    private fun payload(
        minimum: Int = 1,
        current: Int = 2,
        includeUnknownPatch: Boolean = false,
        extraMetadata: Boolean = false,
    ): ByteArray {
        val supportedPatch = JSONObject()
            .put("key", "reference/v2/contracts/1/patch/$sourceSha-$targetSha.mpatch")
            .put("format", "medicine-chunk-v1")
            .put("from_sha256", sourceSha)
            .put("from_size_bytes", 1900)
            .put("sha256", patchSha)
            .put("size_bytes", 200)
        val patches = org.json.JSONArray()
        if (includeUnknownPatch) {
            patches.put(
                JSONObject()
                    .put("format", "future-patch-v9")
                    .put("key", "reference/v2/contracts/1/patch/future.bin")
                    .put("server_diagnostic", "ignored"),
            )
        }
        patches.put(supportedPatch)
        val contract = JSONObject()
            .put("dataset_id", datasetId)
            .put("target", JSONObject().put("sha256", targetSha).put("size_bytes", 2000))
            .put(
                "full",
                JSONObject()
                    .put("key", "reference/v2/contracts/1/full/$targetSha.sqlite.gz")
                    .put("compression", "gzip")
                    .put("sha256", fullSha)
                    .put("size_bytes", 500),
            )
            .put("patches", patches)
            .put("history", org.json.JSONArray())
        if (extraMetadata) contract.put("server_history_limit", 3)
        val contracts = JSONObject().put("1", contract)
        if (current >= 2) {
            contracts.put(
                "2",
                JSONObject()
                    .put("dataset_id", "sha256:" + "f".repeat(64))
                    .put("opaque_future_contract_metadata", true),
            )
        }
        val root = JSONObject()
            .put("protocol_version", 2)
            .put("created_at", "2026-08-20T00:00:00Z")
            .put("current_contract_major", current)
            .put("minimum_supported_contract_major", minimum)
            .put("contracts", contracts)
        if (extraMetadata) root.put("server_diagnostic", "ignored")
        return root.toString().toByteArray(Charsets.UTF_8)
    }

    @Test
    fun selectsOnlyOwnContractAndToleratesAdditiveServerMetadata() {
        val bytes = payload(extraMetadata = true)
        val release = ReferenceReleaseProtocolV2.parseVerifiedRoot(41, bytes, 1)

        assertEquals(1, release.contractMajor)
        assertEquals(datasetId, release.datasetId)
        assertEquals(targetSha, release.targetSha256)
        assertEquals(sha(bytes), release.rootHash)
    }

    @Test
    fun unsupportedPatchFormatIsIgnoredAndFullFallbackRemainsAvailable() {
        val release = ReferenceReleaseProtocolV2.parseVerifiedRoot(
            42,
            payload(includeUnknownPatch = true),
            1,
        )

        assertEquals(1, release.patches.size)
        assertEquals(ReferenceArtifactKind.CHUNK_PATCH, release.patches.single().kind)
        assertEquals(ReferenceArtifactKind.FULL_GZIP, release.full.kind)
    }

    @Test
    fun signedMinimumAboveAppContractIsExplicitRetirement() {
        val bytes = payload(minimum = 2, current = 2)
        val error = try {
            ReferenceReleaseProtocolV2.parseVerifiedRoot(43, bytes, 1)
            throw AssertionError("expected retirement")
        } catch (error: ReferenceContractRetiredException) {
            error
        }

        assertEquals(43, error.releaseSequence)
        assertEquals(sha(bytes), error.rootHash)
        assertEquals(2, error.minimumSupportedContractMajor)
    }

    private fun sha(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}