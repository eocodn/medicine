package com.medicine.android

import java.io.File

class MedicineNativeCore(
    referenceDatabase: File?,
    referenceUnavailableReason: String? = null,
) : AutoCloseable {
    private val lock = Any()
    private var handle: Long = nativeCreate(
        referenceDatabase?.absolutePath,
        referenceUnavailableReason,
    ).also { created -> check(created != 0L) { "native medicine engine initialization failed" } }

    fun requestAccess(method: String, path: String): String = synchronized(lock) {
        nativeRequestAccess(requireOpen(), method, path)
    }

    fun handlesRequest(method: String, path: String): Boolean = synchronized(lock) {
        nativeHandlesRequest(requireOpen(), method, path)
    }

    fun request(method: String, path: String, body: String): String = synchronized(lock) {
        nativeRequest(requireOpen(), method, path, body)
    }

    fun setReferenceAvailable(available: Boolean, reason: String? = null) = synchronized(lock) {
        nativeSetReferenceAvailable(requireOpen(), available, reason)
    }

    override fun close() = synchronized(lock) {
        if (handle != 0L) {
            nativeDestroy(handle)
            handle = 0L
        }
    }

    private fun requireOpen(): Long = handle.takeIf { it != 0L }
        ?: error("native medicine engine is closed")

    private external fun nativeCreate(
        canonicalDb: String?,
        referenceUnavailableReason: String?,
    ): Long

    private external fun nativeDestroy(handle: Long)

    private external fun nativeRequestAccess(handle: Long, method: String, path: String): String

    private external fun nativeHandlesRequest(handle: Long, method: String, path: String): Boolean

    private external fun nativeRequest(handle: Long, method: String, path: String, body: String): String

    private external fun nativeSetReferenceAvailable(handle: Long, available: Boolean, reason: String?)

    companion object {
        init {
            System.loadLibrary("medicine_core")
        }
    }
}
