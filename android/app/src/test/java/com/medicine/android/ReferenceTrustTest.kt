package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Test
import java.security.MessageDigest

class ReferenceTrustTest {
    @Test
    fun productionTrustKeysMatchReviewedSpkiFingerprints() {
        assertEquals(ReferenceTrust.reviewedSpkiSha256.keys, ReferenceTrust.trustedPublicKeys.keys)
        ReferenceTrust.trustedPublicKeys.forEach { (keyId, publicKey) ->
            val fingerprint = MessageDigest.getInstance("SHA-256")
                .digest(publicKey)
                .joinToString("") { "%02x".format(it.toInt() and 0xff) }
            assertEquals(ReferenceTrust.reviewedSpkiSha256[keyId], fingerprint)
        }
        assertEquals(
            "96de63a5e5cb2d1233dc09d13f2e1da8148b97a2cb15e69346a01514287969ea",
            ReferenceTrust.reviewedSpkiSha256["reference-prod-2026-01"],
        )
    }
}
