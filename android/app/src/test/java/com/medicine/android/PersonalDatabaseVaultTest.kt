package com.medicine.android

import java.nio.file.Files
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonalDatabaseVaultTest {
    private fun key() = SecretKeySpec(ByteArray(32) { (it + 1).toByte() }, "AES")

    @Test
    fun sealEncryptsAndRemovesPlaintextUntilNextUse() {
        val dir = Files.createTempDirectory("medicine-vault").toFile()
        val plain = dir.resolve("personal.sqlite")
        val encrypted = dir.resolve("personal.sqlite.enc")
        plain.writeText("private-health-data")
        val vault = PersonalDatabaseVault(plain, encrypted) { key() }

        vault.sealAfterUse()

        assertFalse(plain.exists())
        assertTrue(encrypted.exists())
        assertFalse(encrypted.readBytes().toString(Charsets.ISO_8859_1).contains("private-health-data"))

        vault.openForUse()
        assertEquals("private-health-data", plain.readText())
    }

    @Test
    fun crashLeftoverPlaintextRemainsAuthoritativeAndIsResealed() {
        val dir = Files.createTempDirectory("medicine-vault-recovery").toFile()
        val plain = dir.resolve("personal.sqlite")
        val encrypted = dir.resolve("personal.sqlite.enc")
        val vault = PersonalDatabaseVault(plain, encrypted) { key() }
        plain.writeText("old")
        vault.sealAfterUse()
        vault.openForUse()
        plain.writeText("newer-committed-state")

        vault.sealAfterUse()
        vault.openForUse()

        assertEquals("newer-committed-state", plain.readText())
    }

    @Test
    fun tamperedCiphertextIsRejectedByGcmAuthentication() {
        val dir = Files.createTempDirectory("medicine-vault-tamper").toFile()
        val plain = dir.resolve("personal.sqlite")
        val encrypted = dir.resolve("personal.sqlite.enc")
        val vault = PersonalDatabaseVault(plain, encrypted) { key() }
        plain.writeText("private-health-data")
        vault.sealAfterUse()
        val bytes = encrypted.readBytes()
        bytes[bytes.lastIndex] = (bytes.last().toInt() xor 1).toByte()
        encrypted.writeBytes(bytes)

        assertThrows(Exception::class.java) { vault.openForUse() }
        assertFalse(plain.exists())
    }

    @Test
    fun readOnlyUseOfSealedSnapshotCanDiscardPlaintextWithoutReencrypting() {
        val dir = Files.createTempDirectory("medicine-vault-readonly").toFile()
        val plain = dir.resolve("personal.sqlite")
        val encrypted = dir.resolve("personal.sqlite.enc")
        plain.writeText("private-health-data")
        val vault = PersonalDatabaseVault(plain, encrypted) { key() }
        vault.sealAfterUse()
        val encryptedBefore = encrypted.readBytes()

        val opened = vault.openForUse()
        val discarded = vault.finishReadOnlyUse(opened)

        assertTrue(discarded)
        assertFalse(plain.exists())
        assertTrue(encryptedBefore.contentEquals(encrypted.readBytes()))
    }

    @Test
    fun crashRecoveryPlaintextCannotBeDiscardedAsReadOnlySnapshot() {
        val dir = Files.createTempDirectory("medicine-vault-readonly-recovery").toFile()
        val plain = dir.resolve("personal.sqlite")
        val encrypted = dir.resolve("personal.sqlite.enc")
        val vault = PersonalDatabaseVault(plain, encrypted) { key() }
        plain.writeText("old")
        vault.sealAfterUse()
        vault.openForUse()
        plain.writeText("newer-committed-state")

        val opened = vault.openForUse()

        assertFalse(vault.finishReadOnlyUse(opened))
        assertTrue(plain.exists())
    }
}
