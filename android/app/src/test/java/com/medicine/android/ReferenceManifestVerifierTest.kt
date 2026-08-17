package com.medicine.android

import java.util.Base64
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ReferenceManifestVerifierTest {
    private val publicKey = Base64.getDecoder().decode(
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEPI67A47esbrnylrrO7WqAaSUwlSj" +
            "9REIzwEkQlWQb4L3vx8tR5DSDl80GkuBe8cFmWJ4YtbS0n2nt4uKKPyxAA=="
    )
    private val verifier = ReferenceManifestVerifier(mapOf("test-2026" to publicKey))
    private val payloadBase64 =
        "eyJkYXRhc2V0X2lkIjoic2hhMjU2OmFuZHJvaWQtZml4dHVyZSIsInNjaGVtYV92ZXJzaW9uIjoxfQo="
    private val signatureBase64 =
        "MEUCIATBn3O5nGmYpbcMJbWLrGxMAkW7KUiSzUL6kxX0M9zSAiEA+JEUVIHbLxxZWE3Ofht8NVw6WBX+3d+2o5tdodnICsc="

    @Test
    fun pythonSignedFixtureVerifiesWithExactPayloadBytes() {
        val verified = verifier.verify(
            envelopeVersion = 1,
            algorithm = "ECDSA_P256_SHA256",
            keyId = "test-2026",
            releaseSequence = 77,
            payloadBase64 = payloadBase64,
            signatureBase64 = signatureBase64,
            minimumExclusiveSequence = 76,
        )

        assertEquals(77, verified.releaseSequence)
        assertEquals("test-2026", verified.keyId)
        assertArrayEquals(
            "{\"dataset_id\":\"sha256:android-fixture\",\"schema_version\":1}\n".toByteArray(),
            verified.payload,
        )
    }

    @Test
    fun tamperingUnknownKeyAndReplayAreRejected() {
        val tamperedPayload = Base64.getEncoder().encodeToString("tampered\n".toByteArray())
        assertThrows(IllegalArgumentException::class.java) {
            verifier.verify(
                1,
                "ECDSA_P256_SHA256",
                "test-2026",
                77,
                tamperedPayload,
                signatureBase64,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            ReferenceManifestVerifier(emptyMap()).verify(
                1,
                "ECDSA_P256_SHA256",
                "test-2026",
                77,
                payloadBase64,
                signatureBase64,
            )
        }
        assertThrows(IllegalArgumentException::class.java) {
            verifier.verify(
                1,
                "ECDSA_P256_SHA256",
                "test-2026",
                77,
                payloadBase64,
                signatureBase64,
                minimumExclusiveSequence = 77,
            )
        }
    }

    @Test
    fun malformedAndNonCanonicalBase64AreRejectedBeforeSignatureVerification() {
        for (invalidPayload in listOf("A===", "AB==", "AAAA A==")) {
            assertThrows(IllegalArgumentException::class.java) {
                verifier.verify(
                    1,
                    "ECDSA_P256_SHA256",
                    "test-2026",
                    77,
                    invalidPayload,
                    signatureBase64,
                )
            }
        }
    }

}