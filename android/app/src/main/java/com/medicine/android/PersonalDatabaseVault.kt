package com.medicine.android

import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import javax.crypto.Cipher
import javax.crypto.CipherInputStream
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class PersonalDatabaseVault(
    private val plain: File,
    private val encrypted: File,
    private val keyProvider: () -> SecretKey,
) {
    private val backup = File(encrypted.parentFile, "${encrypted.name}.bak")
    private val sealTemp = File(encrypted.parentFile, "${encrypted.name}.tmp")
    private val openTemp = File(plain.parentFile, "${plain.name}.tmp")

    fun openForUse() {
        // A plaintext DB left by process death may contain a committed request
        // newer than the encrypted snapshot. SQLite recovery runs on that file,
        // then MedicineBridge checkpoints and reseals it immediately.
        if (plain.exists()) return
        val source = when {
            encrypted.isFile -> encrypted
            backup.isFile -> backup
            else -> return
        }
        openTemp.delete()
        decrypt(source, openTemp)
        check(openTemp.renameTo(plain)) { "could not open personal database vault" }
    }

    fun sealAfterUse() {
        if (!plain.isFile) return
        sealTemp.delete()
        encrypt(plain, sealTemp)

        backup.delete()
        if (encrypted.exists()) {
            check(encrypted.renameTo(backup)) { "could not rotate encrypted personal database" }
        }
        if (!sealTemp.renameTo(encrypted)) {
            if (backup.exists() && !encrypted.exists()) backup.renameTo(encrypted)
            throw IllegalStateException("could not commit encrypted personal database")
        }
        backup.delete()
        check(plain.delete()) { "could not remove plaintext personal database" }
        deleteIfPresent(File(plain.path + "-wal"))
        deleteIfPresent(File(plain.path + "-shm"))
    }

    private fun encrypt(source: File, target: File) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, keyProvider())
        check(cipher.iv.size == IV_SIZE) { "unexpected personal database vault IV size" }
        FileOutputStream(target).use { raw ->
            raw.write(MAGIC)
            raw.write(cipher.iv)
            FileInputStream(source).use { input ->
                val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    cipher.update(buffer, 0, count)?.let(raw::write)
                }
            }
            raw.write(cipher.doFinal())
            raw.fd.sync()
        }
    }

    private fun decrypt(source: File, target: File) {
        FileInputStream(source).use { raw ->
            val magic = readExactly(raw, MAGIC.size)
            check(magic.contentEquals(MAGIC)) { "invalid personal database vault header" }
            val iv = readExactly(raw, IV_SIZE)
            check(iv.size == IV_SIZE) { "invalid personal database vault IV" }
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, keyProvider(), GCMParameterSpec(128, iv))
            FileOutputStream(target).use { output ->
                CipherInputStream(raw, cipher).use { decrypted -> decrypted.copyTo(output) }
                output.fd.sync()
            }
        }
    }

    private fun readExactly(input: FileInputStream, size: Int): ByteArray {
        val result = ByteArray(size)
        var offset = 0
        while (offset < size) {
            val count = input.read(result, offset, size - offset)
            if (count < 0) return result.copyOf(offset)
            offset += count
        }
        return result
    }

    private fun deleteIfPresent(file: File) {
        if (file.exists()) check(file.delete()) { "could not remove ${file.name}" }
    }

    companion object {
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val IV_SIZE = 12
        private val MAGIC = "MEDDB1".toByteArray(Charsets.US_ASCII)
    }
}
