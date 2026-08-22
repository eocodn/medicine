package com.medicine.android

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
    ): VerifiedReferenceManifestSignature = ReferenceNativeCore.verifyManifest(
        trustedPublicKeys,
        envelopeVersion,
        algorithm,
        keyId,
        releaseSequence,
        payloadBase64,
        signatureBase64,
        minimumExclusiveSequence,
    )
}
