package com.medicine.android

import org.json.JSONObject
import java.io.File

data class InstalledReference(
    val database: File?,
    val recoveryReason: String? = null,
    val unavailableReason: String? = null,
) {
    val referenceAvailable: Boolean
        get() = database != null
    val referenceUnavailableReason: String?
        get() = if (referenceAvailable) null else (unavailableReason ?: "update_required")
}

data class ReferenceRuntimeOperation(
    val selection: InstalledReference?,
    val snapshot: String,
    val error: String?,
)

enum class ReferenceUpdateStatus {
    STAGED,
    UP_TO_DATE,
    UPDATE_REQUIRED,
    FAILED,
}

data class ReferenceUpdateResult(
    val status: ReferenceUpdateStatus,
    val detail: String? = null,
)

internal fun decodeReferenceRuntimeOperation(raw: String): ReferenceRuntimeOperation {
    val document = JSONObject(raw)
    val selectionObject = document.optJSONObject("selection")
    val selection = selectionObject?.let { value ->
        val database = if (value.isNull("database_path")) {
            null
        } else {
            value.optString("database_path").takeIf { it.isNotEmpty() }?.let(::File)
        }
        InstalledReference(
            database = database,
            unavailableReason = if (value.isNull("unavailable_reason")) {
                null
            } else {
                value.optString("unavailable_reason").takeIf { it.isNotEmpty() }
            },
        )
    }
    return ReferenceRuntimeOperation(
        selection = selection,
        snapshot = document.getJSONObject("snapshot").toString(),
        error = if (document.isNull("error")) null else document.optString("error").takeIf { it.isNotEmpty() },
    )
}

object ReferenceNativeCore {
    fun createReferenceRuntime(referenceDir: File, baseUrl: String): Long =
        nativeCreateReferenceRuntime(
            referenceDir.absolutePath,
            baseUrl,
            BuildConfig.REFERENCE_TRUSTED_KEYS_JSON,
        ).also { handle ->
            check(handle != 0L) { "native reference runtime initialization failed" }
        }

    fun destroyReferenceRuntime(handle: Long) = nativeDestroyReferenceRuntime(handle)

    fun prepare(handle: Long): ReferenceRuntimeOperation =
        decodeReferenceRuntimeOperation(nativeReferencePrepare(handle))

    fun start(handle: Long): ReferenceRuntimeOperation =
        decodeReferenceRuntimeOperation(nativeReferenceStart(handle))

    fun status(handle: Long): String = nativeReferenceStatus(handle)

    fun checkForUpdate(handle: Long): ReferenceUpdateResult {
        val result = JSONObject(nativeReferenceCheckForUpdate(handle))
        return when (result.getString("status")) {
            "no_change" -> ReferenceUpdateResult(ReferenceUpdateStatus.UP_TO_DATE)
            "staged" -> ReferenceUpdateResult(ReferenceUpdateStatus.STAGED)
            "update_required" -> ReferenceUpdateResult(ReferenceUpdateStatus.UPDATE_REQUIRED)
            "failed" -> ReferenceUpdateResult(
                ReferenceUpdateStatus.FAILED,
                result.optString("detail").takeIf { it.isNotEmpty() },
            )
            else -> error("native reference update returned an invalid status")
        }
    }

    private external fun nativeCreateReferenceRuntime(
        referenceDir: String,
        baseUrl: String,
        trustedKeysJson: String,
    ): Long

    private external fun nativeDestroyReferenceRuntime(handle: Long)
    private external fun nativeReferencePrepare(handle: Long): String
    private external fun nativeReferenceStart(handle: Long): String
    private external fun nativeReferenceStatus(handle: Long): String
    private external fun nativeReferenceCheckForUpdate(handle: Long): String

    init {
        System.loadLibrary("medicine_core")
    }

}
