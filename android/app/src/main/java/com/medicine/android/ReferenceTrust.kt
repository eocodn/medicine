package com.medicine.android

import org.json.JSONObject

object ReferenceTrust {
    val trustedPublicKeys: Map<String, ByteArray> =
        parseTrustedPublicKeys(BuildConfig.REFERENCE_TRUSTED_KEYS_JSON)

    internal fun parseTrustedPublicKeys(raw: String): Map<String, ByteArray> {
        val document = JSONObject(raw)
        require(document.length() > 0) { "reference signing trust set is empty" }
        val trusted = linkedMapOf<String, ByteArray>()
        val keys = document.keys()
        while (keys.hasNext()) {
            val keyId = keys.next()
            require(Regex("[A-Za-z0-9._-]{1,64}").matches(keyId)) {
                "invalid reference signing key ID: $keyId"
            }
            val encoded = document.opt(keyId)
            require(encoded is String) { "invalid reference signing public key: $keyId" }
            trusted[keyId] = decodeHex(encoded)
        }
        return trusted
    }

    private fun decodeHex(value: String): ByteArray {
        require(value.isNotEmpty() && value.length % 2 == 0) {
            "invalid trusted public key encoding"
        }
        return ByteArray(value.length / 2) { index ->
            val offset = index * 2
            value.substring(offset, offset + 2).toInt(16).toByte()
        }
    }
}
