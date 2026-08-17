package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Test
import java.security.MessageDigest

class ReferenceTrustTest {
    @Test
    fun productionTrustKeyMatchesReviewedSpkiFingerprint() {
        val publicKey = requireNotNull(ReferenceTrust.trustedPublicKeys[ReferenceTrust.PRODUCTION_KEY_ID])
        val fingerprint = MessageDigest.getInstance("SHA-256")
            .digest(publicKey)
            .joinToString("") { "%02x".format(it) }
        assertEquals(ReferenceTrust.PRODUCTION_SPKI_SHA256, fingerprint)
        assertEquals("96de63a5e5cb2d1233dc09d13f2e1da8148b97a2cb15e69346a01514287969ea", fingerprint)
    }
}
