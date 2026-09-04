package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Test

class MedicineNativeProxyTest {
    private data class Received(
        val requestId: String,
        val method: String,
        val path: String,
        val body: String?,
        val coalesceKey: String?,
    )

    private class FakeDelegate : MedicineRequestDelegate {
        val received = mutableListOf<Received>()

        override fun requestAsync(
            requestId: String,
            method: String,
            path: String,
            body: String?,
            coalesceKey: String?,
        ) {
            received += Received(requestId, method, path, body, coalesceKey)
        }
    }

    @Test
    fun requestBeforeAttachIsDeliveredExactlyOnceAfterAttach() {
        val proxy = MedicineNativeProxy()
        val delegate = FakeDelegate()

        proxy.requestAsync("native-1", "GET", "/api/people", "", "people")

        assertEquals(emptyList<Received>(), delegate.received)

        proxy.attach(delegate)

        assertEquals(
            listOf(Received("native-1", "GET", "/api/people", "", "people")),
            delegate.received,
        )
    }

    @Test
    fun attachedRequestsPreserveOrderAcrossQueuedAndLiveRequests() {
        val proxy = MedicineNativeProxy()
        val delegate = FakeDelegate()

        proxy.requestAsync("native-1", "GET", "/api/people", "", "people")
        proxy.requestAsync("native-2", "GET", "/api/reminders/upcoming", "", "reminders")
        proxy.attach(delegate)
        proxy.requestAsync("native-3", "GET", "/api/people/p1/dashboard", "", "dashboard:p1")

        assertEquals(listOf("native-1", "native-2", "native-3"), delegate.received.map { it.requestId })
    }
}