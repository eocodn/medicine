package com.medicine.android

import android.content.Intent
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReminderSystemEventPolicyTest {
    @Test
    fun rebootAndPackageReplacementRebuildAllAlarms() {
        assertTrue(ReminderSystemEventPolicy.replaceAll(Intent.ACTION_BOOT_COMPLETED))
        assertTrue(ReminderSystemEventPolicy.replaceAll(Intent.ACTION_MY_PACKAGE_REPLACED))
        assertFalse(ReminderSystemEventPolicy.replaceAll(ReminderScheduler.ACTION_MAINTENANCE))
    }
}
