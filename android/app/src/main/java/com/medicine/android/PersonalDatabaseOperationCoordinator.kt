package com.medicine.android

import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

object PersonalDatabaseOperationCoordinator {
    // MainActivity can be recreated while an old bridge still finishes a write.
    // The vault paths are process-global, so every bridge must share this boundary.
    private val lock = ReentrantLock(true)

    fun <T> exclusive(block: () -> T): T = lock.withLock(block)
}