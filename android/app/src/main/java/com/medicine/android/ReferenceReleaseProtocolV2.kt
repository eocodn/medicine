package com.medicine.android

internal object ReferenceReleaseProtocolV2 {
    const val ROOT_KEY = "reference/v2/latest.json"

    fun parseVerifiedRoot(
        releaseSequence: Long,
        payload: ByteArray,
        contractMajor: Int,
    ): VerifiedReferenceRelease =
        ReferenceNativeCore.parseReleaseRoot(releaseSequence, payload, contractMajor)
}
