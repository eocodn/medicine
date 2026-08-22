package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertArrayEquals
import org.junit.Test
import java.security.MessageDigest

class ReferenceTrustTest {
    @Test
    fun productionTrustKeysComeFromGeneratedBuildConfig() {
        val configured = ReferenceTrust.parseTrustedPublicKeys(BuildConfig.REFERENCE_TRUSTED_KEYS_JSON)
        assertEquals(configured.keys, ReferenceTrust.trustedPublicKeys.keys)
        configured.forEach { (keyId, publicKey) ->
            assertArrayEquals(publicKey, ReferenceTrust.trustedPublicKeys[keyId])
        }

        val current = requireNotNull(ReferenceTrust.trustedPublicKeys["reference-prod-2026-01"])
        val fingerprint = MessageDigest.getInstance("SHA-256")
            .digest(current)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
        assertEquals(
            "96de63a5e5cb2d1233dc09d13f2e1da8148b97a2cb15e69346a01514287969ea",
            fingerprint,
        )
    }

    @Test
    fun trustParserAcceptsMultipleDistinctKeys() {
        val trusted = ReferenceTrust.parseTrustedPublicKeys("{\"old-key\":\"aa\",\"new-key\":\"bb\"}")
        assertEquals(setOf("old-key", "new-key"), trusted.keys)
        assertArrayEquals(byteArrayOf(0xaa.toByte()), trusted["old-key"])
        assertArrayEquals(byteArrayOf(0xbb.toByte()), trusted["new-key"])
    }
}
