package com.medicine.android

import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.math.BigInteger
import java.security.KeyFactory
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.X509EncodedKeySpec

data class VerifiedReferenceManifestSignature(
    val keyId: String,
    val releaseSequence: Long,
    val payload: ByteArray,
)

class ReferenceManifestVerifier(
    private val trustedPublicKeys: Map<String, ByteArray>,
) {
    fun verify(
        envelopeVersion: Int,
        algorithm: String,
        keyId: String,
        releaseSequence: Long,
        payloadBase64: String,
        signatureBase64: String,
        minimumExclusiveSequence: Long? = null,
    ): VerifiedReferenceManifestSignature {
        require(envelopeVersion == ENVELOPE_VERSION) { "unsupported release envelope version" }
        require(algorithm == ALGORITHM) { "unsupported release signature algorithm" }
        require(KEY_ID.matches(keyId)) { "invalid release signing key id" }
        require(releaseSequence > 0) { "invalid release sequence" }
        if (minimumExclusiveSequence != null) {
            require(minimumExclusiveSequence > 0) { "invalid accepted release sequence" }
            require(releaseSequence > minimumExclusiveSequence) { "release sequence is not newer" }
        }

        val publicKeyBytes = requireNotNull(trustedPublicKeys[keyId]) {
            "untrusted release signing key"
        }
        val payload = decodeBase64(payloadBase64, "release payload")
        val signatureBytes = decodeBase64(signatureBase64, "release signature")
        require(payload.isNotEmpty()) { "release payload is empty" }
        require(signatureBytes.isNotEmpty()) { "release signature is empty" }

        val publicKey = try {
            KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(publicKeyBytes)) as ECPublicKey
        } catch (error: Exception) {
            throw IllegalArgumentException("invalid release signing public key", error)
        }
        require(publicKey.params.curve.field.fieldSize == 256) { "release signing key is not P-256" }
        require(publicKey.params.order == P256_ORDER) { "release signing key is not P-256" }
        require(publicKey.params.cofactor == 1) { "release signing key is not P-256" }

        val verifier = Signature.getInstance("SHA256withECDSA")
        verifier.initVerify(publicKey)
        verifier.update(signingMessage(keyId, releaseSequence, payload))
        require(verifier.verify(signatureBytes)) { "release manifest signature is invalid" }

        return VerifiedReferenceManifestSignature(keyId, releaseSequence, payload)
    }

    private fun decodeBase64(value: String, label: String): ByteArray {
        require(value.isNotEmpty() && value.length % 4 == 0) { "invalid base64 $label" }
        val output = ByteArrayOutputStream(value.length / 4 * 3)
        for (offset in value.indices step 4) {
            val a = decodeBase64Char(value[offset])
            val b = decodeBase64Char(value[offset + 1])
            val third = value[offset + 2]
            val fourth = value[offset + 3]
            val c = if (third == '=') -1 else decodeBase64Char(third)
            val d = if (fourth == '=') -1 else decodeBase64Char(fourth)
            val isLast = offset + 4 == value.length

            require(a >= 0 && b >= 0) { "invalid base64 $label" }
            require(c >= 0 || (isLast && third == '=' && fourth == '=')) {
                "invalid base64 $label"
            }
            require(d >= 0 || (isLast && fourth == '=')) { "invalid base64 $label" }

            output.write((a shl 2) or (b ushr 4))
            if (c >= 0) {
                output.write(((b and 0x0f) shl 4) or (c ushr 2))
                if (d >= 0) {
                    output.write(((c and 0x03) shl 6) or d)
                } else {
                    require(c and 0x03 == 0) { "non-canonical base64 $label" }
                }
            } else {
                require(b and 0x0f == 0) { "non-canonical base64 $label" }
            }
        }
        return output.toByteArray()
    }

    private fun decodeBase64Char(value: Char): Int = when (value) {
        in 'A'..'Z' -> value.code - 'A'.code
        in 'a'..'z' -> value.code - 'a'.code + 26
        in '0'..'9' -> value.code - '0'.code + 52
        '+' -> 62
        '/' -> 63
        else -> -1
    }

    private fun signingMessage(keyId: String, releaseSequence: Long, payload: ByteArray): ByteArray {
        // Keep this byte-for-byte aligned with medicine_canonical.release_signing.
        // The sequence is signed so a valid old payload cannot be relabeled as new.
        val keyBytes = keyId.toByteArray(Charsets.US_ASCII)
        val output = ByteArrayOutputStream()
        DataOutputStream(output).use { framed ->
            framed.write(SIGNATURE_MAGIC)
            framed.writeInt(keyBytes.size)
            framed.writeLong(releaseSequence)
            framed.writeLong(payload.size.toLong())
            framed.write(keyBytes)
            framed.write(payload)
        }
        return output.toByteArray()
    }

    companion object {
        private const val ENVELOPE_VERSION = 1
        private const val ALGORITHM = "ECDSA_P256_SHA256"
        private val KEY_ID = Regex("[A-Za-z0-9._-]{1,64}")
        private val SIGNATURE_MAGIC = "MEDREFSIG1".toByteArray(Charsets.US_ASCII)
        private val P256_ORDER = BigInteger(
            "FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551",
            16,
        )
    }
}