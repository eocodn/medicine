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
        assertEquals("d75d79623ff8016b7e1b12665d0d4edea0551ab8f299ab08ebe7d35a73bee4fa", fingerprint)
    }
}
