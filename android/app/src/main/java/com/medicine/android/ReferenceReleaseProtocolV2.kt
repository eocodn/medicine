package com.medicine.android

import org.json.JSONObject
import java.security.MessageDigest

internal object ReferenceReleaseProtocolV2 {
    const val ROOT_KEY = "reference/v2/latest.json"
    private const val PROTOCOL_VERSION = 2
    private const val PATCH_FORMAT = "medicine-chunk-v1"

    fun parseVerifiedRoot(
        releaseSequence: Long,
        payload: ByteArray,
        contractMajor: Int,
    ): VerifiedReferenceRelease {
        require(releaseSequence > 0) { "invalid reference root sequence" }
        require(contractMajor > 0) { "invalid reference contract major" }
        val rootHash = sha256(payload)
        val root = JSONObject(String(payload, Charsets.UTF_8))
        require(root.getInt("protocol_version") == PROTOCOL_VERSION) {
            "unsupported reference release protocol"
        }
        val current = root.getInt("current_contract_major")
        val minimum = root.getInt("minimum_supported_contract_major")
        require(current > 0 && minimum > 0 && minimum <= current) {
            "invalid reference contract support window"
        }
        require(current - minimum <= 1) { "reference contract support window exceeds N/N-1" }
        if (contractMajor < minimum) {
            throw ReferenceContractRetiredException(
                releaseSequence = releaseSequence,
                rootHash = rootHash,
                currentContractMajor = current,
                minimumSupportedContractMajor = minimum,
            )
        }
        require(contractMajor <= current) {
            "signed reference root does not yet support this app contract"
        }
        val contracts = root.getJSONObject("contracts")
        require(contracts.has(contractMajor.toString())) {
            "signed reference root omits a supported contract entry"
        }
        val entry = contracts.getJSONObject(contractMajor.toString())
        val target = entry.getJSONObject("target")
        val targetSha = target.getString("sha256")
        val targetSize = target.getLong("size_bytes")

        val fullJson = entry.getJSONObject("full")
        require(fullJson.getString("compression") == "gzip") {
            "unsupported reference full compression"
        }
        val full = ReferenceReleaseArtifact(
            contractMajor = contractMajor,
            key = fullJson.getString("key"),
            sha256 = fullJson.getString("sha256"),
            sizeBytes = fullJson.getLong("size_bytes"),
            kind = ReferenceArtifactKind.FULL_GZIP,
        )

        val patchesJson = entry.getJSONArray("patches")
        val patches = ArrayList<ReferenceReleaseArtifact>(patchesJson.length())
        for (index in 0 until patchesJson.length()) {
            val patch = patchesJson.getJSONObject(index)
            // Patch format is an optimization, not a compatibility gate. Unknown
            // codecs are ignored so every supported contract keeps full-gzip as
            // the mandatory conservative fallback.
            if (patch.optString("format") != PATCH_FORMAT) continue
            patches += ReferenceReleaseArtifact(
                contractMajor = contractMajor,
                key = patch.getString("key"),
                sha256 = patch.getString("sha256"),
                sizeBytes = patch.getLong("size_bytes"),
                kind = ReferenceArtifactKind.CHUNK_PATCH,
                fromSha256 = patch.getString("from_sha256"),
                fromSizeBytes = patch.getLong("from_size_bytes"),
            )
        }

        return VerifiedReferenceRelease(
            releaseSequence = releaseSequence,
            rootHash = rootHash,
            datasetId = entry.getString("dataset_id"),
            contractMajor = contractMajor,
            targetSha256 = targetSha,
            targetSizeBytes = targetSize,
            full = full,
            patches = patches,
        )
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256")
            .digest(bytes)
            .joinToString("") { "%02x".format(it) }
}
