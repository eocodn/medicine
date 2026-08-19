package com.medicine.android

import java.util.concurrent.locks.ReentrantLock

/**
 * Serializes reference release I/O across independently recreated Activity objects.
 * Activity executor interruption is best-effort, so correctness must not depend on
 * an old worker having stopped before a new Activity starts another operation.
 */
object ReferenceOperationCoordinator {
    private val lock = ReentrantLock(true)

    fun <T> exclusive(block: () -> T): T {
        lock.lock()
        return try {
            block()
        } finally {
            lock.unlock()
        }
    }
}