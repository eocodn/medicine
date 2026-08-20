package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class BridgeRequestDispatcherTest {
    @Test
    fun latestOnlyQueueDropsPendingDuplicatesButNeverInterruptsStartedWork() {
        val executor = Executors.newSingleThreadExecutor()
        val firstStarted = CountDownLatch(1)
        val releaseFirst = CountDownLatch(1)
        val completed = CountDownLatch(3)
        val processed = Collections.synchronizedList(mutableListOf<String>())
        val responses = Collections.synchronizedMap(mutableMapOf<String, String>())
        val dispatcher = BridgeRequestDispatcher(
            executor = executor,
            processor = { request ->
                processed += request.requestId
                if (request.requestId == "one") {
                    firstStarted.countDown()
                    releaseFirst.await(5, TimeUnit.SECONDS)
                }
                "{\"status\":200,\"body\":{\"id\":\"${request.requestId}\"}}"
            },
            responder = { requestId, response ->
                responses[requestId] = response
                completed.countDown()
            },
        )

        dispatcher.submit(BridgeRequest("one", "GET", "/api/products?q=a", "", "product-search"))
        assertTrue(firstStarted.await(5, TimeUnit.SECONDS))
        dispatcher.submit(BridgeRequest("two", "GET", "/api/products?q=ab", "", "product-search"))
        dispatcher.submit(BridgeRequest("three", "GET", "/api/products?q=abc", "", "product-search"))
        releaseFirst.countDown()

        assertTrue(completed.await(5, TimeUnit.SECONDS))
        executor.shutdownNow()
        assertEquals(listOf("one", "three"), processed.toList())
        assertTrue(responses.getValue("two").contains("\"code\":\"superseded\""))
        assertTrue(responses.getValue("one").contains("\"status\":200"))
        assertTrue(responses.getValue("three").contains("\"status\":200"))
    }

    @Test
    fun closeKeepsAcceptedMutationQueuedButMayAbandonDisposableLatestOnlyRead() {
        val executor = Executors.newSingleThreadExecutor()
        val firstStarted = CountDownLatch(1)
        val releaseFirst = CountDownLatch(1)
        val writeProcessed = CountDownLatch(1)
        val processed = Collections.synchronizedList(mutableListOf<String>())
        val responses = Collections.synchronizedMap(mutableMapOf<String, String>())
        val dispatcher = BridgeRequestDispatcher(
            executor = executor,
            processor = { request ->
                processed += request.requestId
                if (request.requestId == "first") {
                    firstStarted.countDown()
                    releaseFirst.await(5, TimeUnit.SECONDS)
                }
                if (request.requestId == "write") writeProcessed.countDown()
                "{\"status\":200,\"body\":{\"id\":\"${request.requestId}\"}}"
            },
            responder = { requestId, response -> responses[requestId] = response },
        )

        dispatcher.submit(BridgeRequest("first", "GET", "/api/people", "", ""))
        assertTrue(firstStarted.await(5, TimeUnit.SECONDS))
        dispatcher.submit(BridgeRequest("write", "POST", "/api/medications/m/prn-intakes", "{}", ""))
        dispatcher.submit(BridgeRequest("search", "GET", "/api/products?q=a", "", "product-search"))

        dispatcher.close()
        releaseFirst.countDown()

        assertTrue(writeProcessed.await(5, TimeUnit.SECONDS))
        executor.shutdown()
        assertTrue(executor.awaitTermination(5, TimeUnit.SECONDS))
        assertEquals(listOf("first", "write"), processed.toList())
        assertTrue(responses.getValue("search").contains("\"code\":\"bridge_closed\""))
        assertTrue(responses.getValue("write").contains("\"status\":200"))
    }
}