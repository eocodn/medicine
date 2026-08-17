package com.medicine.android

object ReferenceTrust {
    const val PRODUCTION_KEY_ID = "reference-prod-2026-01"
    const val PRODUCTION_SPKI_SHA256 = "96de63a5e5cb2d1233dc09d13f2e1da8148b97a2cb15e69346a01514287969ea"

    private const val PRODUCTION_SPKI_HEX =
        "3059301306072a8648ce3d020106082a8648ce3d03010703420004" +
        "2fe843d039b5e12d8fb81526bb8601a548ff8d2c204493856905d25cb3d3332d" +
        "c56f6a2144bb8f2406847505a2604e62501561cedcdb3415bb057f4a14d3866f"

    val trustedPublicKeys: Map<String, ByteArray> = mapOf(
        PRODUCTION_KEY_ID to decodeHex(PRODUCTION_SPKI_HEX),
    )

    private fun decodeHex(value: String): ByteArray {
        require(value.length % 2 == 0) { "invalid trusted public key encoding" }
        return ByteArray(value.length / 2) { index ->
            val offset = index * 2
            value.substring(offset, offset + 2).toInt(16).toByte()
        }
    }
}
