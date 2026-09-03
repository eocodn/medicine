package com.medicine.android

import android.content.Intent
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReminderSystemEventPolicyTest {
    @Test
    fun rebootPackageReplacementAndExactPermissionChangeRebuildAllAlarms() {
        assertTrue(ReminderSystemEventPolicy.replaceAll(Intent.ACTION_BOOT_COMPLETED))
        assertTrue(ReminderSystemEventPolicy.replaceAll(Intent.ACTION_MY_PACKAGE_REPLACED))
        assertTrue(
            ReminderSystemEventPolicy.replaceAll(
                ReminderSystemReceiver.ACTION_EXACT_ALARM_PERMISSION_CHANGED
            )
        )
        assertFalse(ReminderSystemEventPolicy.replaceAll(ReminderScheduler.ACTION_MAINTENANCE))
    }
}
