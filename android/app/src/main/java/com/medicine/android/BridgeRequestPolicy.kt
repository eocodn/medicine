package com.medicine.android

data class BridgeRequestPolicy(
    val mustCompleteAfterClose: Boolean,
    val latestOnlyKey: String?,
) {
    companion object {
        fun classify(method: String, coalesceKey: String): BridgeRequestPolicy {
            // GET work is reconstructible from authoritative state. Accepted
            // mutations are not: they must drain after Activity close and must
            // never inherit latest-only coalescing from a caller-supplied key.
            val reconstructibleRead = method.equals("GET", ignoreCase = true)
            return BridgeRequestPolicy(
                mustCompleteAfterClose = !reconstructibleRead,
                latestOnlyKey = coalesceKey.trim().takeIf { reconstructibleRead && it.isNotEmpty() },
            )
        }
    }
}