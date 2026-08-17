package com.medicine.android

object ReferenceTrust {
    const val PRODUCTION_KEY_ID = "reference-prod-2026-01"
    const val PRODUCTION_SPKI_SHA256 = "d75d79623ff8016b7e1b12665d0d4edea0551ab8f299ab08ebe7d35a73bee4fa"

    private const val PRODUCTION_SPKI_HEX =
        "3059301306072a8648ce3d020106082a8648ce3d03010703420004" +
        "b585dbb4058a6aa0acca308af78337f0d61df40b90a628dbc76782748d69dd72" +
        "062288b4a83543c8a129a571558a3ed3d251c5357c966eabaf971b36c723b1d4"

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
