package com.medicine.android

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
}
