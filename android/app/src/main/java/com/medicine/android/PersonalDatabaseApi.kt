package com.medicine.android

import android.util.Log
import java.io.File
import org.json.JSONObject

data class PersonalDatabaseApiResponse(
    val access: String?,
    val envelope: String,
)

class PersonalDatabaseApi(
    referenceDatabase: File?,
    personalDatabase: File,
    private val vault: PersonalDatabaseVault,
) : AutoCloseable {
    private val apiLock = Any()
    private val nativeCore = MedicineNativeCore(referenceDatabase, personalDatabase)
    private var closed = false

    init {
        try {
            PersonalDatabaseOperationCoordinator.exclusive {
                vault.openForUse()
                try {
                    synchronized(apiLock) { nativeCore.initializePersonalDatabase() }
                } finally {
                    // Initialization may have committed schema/WAL changes before failing.
                    // Never replace the encrypted snapshot until Rust confirms checkpoint completion.
                    synchronized(apiLock) { nativeCore.prepareForSeal() }
                    vault.sealAfterUse()
                }
            }
        } catch (error: Throwable) {
            nativeCore.close()
            throw error
        }
    }

    fun setReferenceAvailable(available: Boolean, reason: String? = null) = synchronized(apiLock) {
        requireOpen()
        nativeCore.setReferenceAvailable(available, reason)
    }

    fun request(method: String, path: String, body: String): String =
        requestWithAccess(method, path, body).envelope

    fun requestWithAccess(method: String, path: String, body: String): PersonalDatabaseApiResponse {
        val access = try {
            synchronized(apiLock) {
                requireOpen()
                nativeCore.requestAccess(method, path)
            }
        } catch (error: Throwable) {
            Log.e(TAG, "Native API access classification failed", error)
            return PersonalDatabaseApiResponse(null, failureEnvelope("native bridge failure"))
        }
        val envelope = when (access) {
            "reference" -> callApi(method, path, body)
            "personal_read" -> callPersonalApi(method, path, body, readOnly = true)
            "personal_write" -> callPersonalApi(method, path, body, readOnly = false)
            else -> {
                Log.e(TAG, "Native API returned unsupported access class: $access")
                failureEnvelope("native bridge failure")
            }
        }
        return PersonalDatabaseApiResponse(access, envelope)
    }

    override fun close() = synchronized(apiLock) {
        if (closed) return
        closed = true
        nativeCore.close()
    }

    private fun callPersonalApi(
        method: String,
        path: String,
        body: String,
        readOnly: Boolean,
    ): String = PersonalDatabaseOperationCoordinator.exclusive {
        val openOrigin = try {
            vault.openForUse()
        } catch (error: Throwable) {
            Log.e(TAG, "Personal database vault open failed", error)
            return@exclusive failureEnvelope("personal data encryption failure")
        }
        var response = callApi(method, path, body)
        try {
            val discardedReadOnlySnapshot = readOnly && vault.finishReadOnlyUse(openOrigin)
            if (!discardedReadOnlySnapshot) {
                synchronized(apiLock) {
                    requireOpen()
                    nativeCore.prepareForSeal()
                }
                vault.sealAfterUse()
            }
        } catch (error: Throwable) {
            Log.e(TAG, "Personal database vault seal failed", error)
            response = failureEnvelope("personal data encryption failure")
        }
        response
    }

    private fun callApi(method: String, path: String, body: String): String = try {
        synchronized(apiLock) {
            requireOpen()
            nativeCore.request(method, path, body)
        }
    } catch (error: Throwable) {
        Log.e(TAG, "Native API request failed", error)
        failureEnvelope("native bridge failure")
    }

    private fun requireOpen() {
        check(!closed) { "personal database API is closed" }
    }

    private fun failureEnvelope(detail: String): String = JSONObject()
        .put("status", 500)
        .put("body", JSONObject().put("detail", detail))
        .toString()

    companion object {
        private const val TAG = "PersonalDatabaseApi"
    }
}
