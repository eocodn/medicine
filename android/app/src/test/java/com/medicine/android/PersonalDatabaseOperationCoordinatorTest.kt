package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class PersonalDatabaseOperationCoordinatorTest {
    @Test
    fun successorLifecycleCannotEnterUntilCurrentPersonalOperationFinishes() {
        val executor = Executors.newFixedThreadPool(2)
        val entered = Collections.synchronizedList(mutableListOf<String>())
        val firstEntered = CountDownLatch(1)
        val releaseFirst = CountDownLatch(1)
        val secondAttempting = CountDownLatch(1)
        val secondEntered = CountDownLatch(1)

        executor.submit {
            PersonalDatabaseOperationCoordinator.exclusive {
                entered += "first"
                firstEntered.countDown()
                releaseFirst.await(5, TimeUnit.SECONDS)
            }
        }
        assertTrue(firstEntered.await(5, TimeUnit.SECONDS))

        executor.submit {
            secondAttempting.countDown()
            PersonalDatabaseOperationCoordinator.exclusive {
                entered += "second"
                secondEntered.countDown()
            }
        }

        assertTrue(secondAttempting.await(5, TimeUnit.SECONDS))
        assertFalse(secondEntered.await(150, TimeUnit.MILLISECONDS))
        releaseFirst.countDown()
        assertTrue(secondEntered.await(5, TimeUnit.SECONDS))
        executor.shutdownNow()
        assertEquals(listOf("first", "second"), entered.toList())
    }

    @Test
    fun medicineBridgeUsesProcessWideBoundaryForInitAndPersonalRequests() {
        val bridge = java.io.File("src/main/java/com/medicine/android/MedicineBridge.kt").readText()
        val uses = "PersonalDatabaseOperationCoordinator.exclusive".toRegex().findAll(bridge).count()

        assertTrue(uses >= 2)
    }
}