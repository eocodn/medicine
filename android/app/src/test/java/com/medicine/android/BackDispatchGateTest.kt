package com.medicine.android

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackDispatchGateTest {
    @Test
    fun duplicateBackIsRejectedWhileJavascriptDecisionIsPending() {
        val gate = BackDispatchGate()

        assertTrue(gate.tryBegin())
        assertFalse(gate.tryBegin())
    }

    @Test
    fun handledJavascriptConsumesBackAndReopensGate() {
        val gate = BackDispatchGate()
        assertTrue(gate.tryBegin())

        assertFalse(gate.complete("true", activityEnding = false))
        assertTrue(gate.tryBegin())
    }

    @Test
    fun unhandledJavascriptDelegatesToDefaultBack() {
        val gate = BackDispatchGate()
        assertTrue(gate.tryBegin())

        assertTrue(gate.complete("false", activityEnding = false))
    }

    @Test
    fun activityTeardownNeverRedispatchesBackAfterJavascriptCompletes() {
        val gate = BackDispatchGate()
        assertTrue(gate.tryBegin())

        assertFalse(gate.complete("false", activityEnding = true))
    }
}
