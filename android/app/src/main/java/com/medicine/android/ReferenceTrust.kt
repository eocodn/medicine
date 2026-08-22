package com.medicine.android

import java.security.MessageDigest

object ReferenceTrust {
    private data class ReviewedKey(
        val keyId: String,
        val spkiHex: String,
        val spkiSha256: String,
    )

    private val productionKeys = listOf(
        ReviewedKey(
            "reference-prod-2026-01",
            "3059301306072a8648ce3d020106082a8648ce3d03010703420004" +
                "2fe843d039b5e12d8fb81526bb8601a548ff8d2c204493856905d25cb3d3332d" +
                "c56f6a2144bb8f2406847505a2604e62501561cedcdb3415bb057f4a14d3866f",
            "96de63a5e5cb2d1233dc09d13f2e1da8148b97a2cb15e69346a01514287969ea",
        ),
    )

    val trustedPublicKeys: Map<String, ByteArray> = productionKeys.associate { key ->
        key.keyId to decodeHex(key.spkiHex)
    }

    val reviewedSpkiSha256: Map<String, String> = productionKeys.associate { key ->
        key.keyId to key.spkiSha256
    }

    init {
        require(trustedPublicKeys.size == productionKeys.size) { "duplicate reference signing key ID" }
        trustedPublicKeys.forEach { (keyId, publicKey) ->
            val fingerprint = MessageDigest.getInstance("SHA-256")
                .digest(publicKey)
                .joinToString("") { "%02x".format(it.toInt() and 0xff) }
            require(reviewedSpkiSha256[keyId] == fingerprint) {
                "reference signing key fingerprint mismatch: $keyId"
            }
        }
    }

    private fun decodeHex(value: String): ByteArray {
        require(value.length % 2 == 0) { "invalid trusted public key encoding" }
        return ByteArray(value.length / 2) { index ->
            val offset = index * 2
            value.substring(offset, offset + 2).toInt(16).toByte()
        }
    }
}
