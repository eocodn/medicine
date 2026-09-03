package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PersonalDataRevisionGateTest {
    @Test
    fun consumesEachRevisionOnlyOnce() {
        val gate = PersonalDataRevisionGate(4)

        assertFalse(gate.consumeIfChanged(4))
        assertTrue(gate.consumeIfChanged(5))
        assertFalse(gate.consumeIfChanged(5))
        assertTrue(gate.consumeIfChanged(7))
    }

    @Test
    fun externalSignalPublishesOnlyWhileSubscribed() {
        val signal = PersonalDataRevisionSignal()
        val seen = mutableListOf<Long>()
        val listener: (Long) -> Unit = { revision -> seen += revision }

        signal.subscribe(listener)
        signal.publish(5)
        signal.unsubscribe(listener)
        signal.publish(6)

        assertEquals(listOf(5L), seen)
    }
}
