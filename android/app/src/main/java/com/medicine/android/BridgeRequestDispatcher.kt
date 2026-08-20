package com.medicine.android

import java.util.ArrayDeque
import java.util.concurrent.Executor

data class BridgeRequest(
    val requestId: String,
    val method: String,
    val path: String,
    val body: String,
    val coalesceKey: String,
) {
    val policy: BridgeRequestPolicy = BridgeRequestPolicy.classify(method, coalesceKey)
}

class BridgeRequestDispatcher(
    private val executor: Executor,
    private val processor: (BridgeRequest) -> String,
    private val responder: (String, String) -> Unit,
) {
    private val lock = Any()
    private val pending = ArrayDeque<BridgeRequest>()
    private var drainScheduled = false
    private var closed = false

    fun submit(request: BridgeRequest) {
        val superseded = mutableListOf<BridgeRequest>()
        var scheduleDrain = false
        synchronized(lock) {
            if (closed) {
                superseded += request
            } else {
                val latestOnlyKey = request.policy.latestOnlyKey
                if (latestOnlyKey != null) {
                    val retained = ArrayDeque<BridgeRequest>()
                    while (pending.isNotEmpty()) {
                        val queued = pending.removeFirst()
                        if (queued.policy.latestOnlyKey == latestOnlyKey) superseded += queued
                        else retained.addLast(queued)
                    }
                    pending.addAll(retained)
                }
                pending.addLast(request)
                if (!drainScheduled) {
                    drainScheduled = true
                    scheduleDrain = true
                }
            }
        }
        superseded.forEach { queued -> responder(queued.requestId, supersededEnvelope()) }
        if (scheduleDrain) executor.execute(::drain)
    }

    fun close() {
        val abandoned = mutableListOf<BridgeRequest>()
        synchronized(lock) {
            closed = true
            val retained = ArrayDeque<BridgeRequest>()
            while (pending.isNotEmpty()) {
                val queued = pending.removeFirst()
                if (queued.policy.mustCompleteAfterClose) retained.addLast(queued)
                else abandoned += queued
            }
            pending.addAll(retained)
        }
        abandoned.forEach { responder(it.requestId, closedEnvelope()) }
    }

    private fun drain() {
        while (true) {
            val request = synchronized(lock) {
                if (pending.isEmpty()) {
                    drainScheduled = false
                    return
                }
                pending.removeFirst()
            }
            responder(request.requestId, processor(request))
        }
    }

    private fun supersededEnvelope(): String =
        "{\"status\":499,\"body\":{\"code\":\"superseded\",\"detail\":\"request superseded\"}}"

    private fun closedEnvelope(): String =
        "{\"status\":503,\"body\":{\"code\":\"bridge_closed\",\"detail\":\"native bridge closed\"}}"
}