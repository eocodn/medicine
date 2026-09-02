package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReferenceNativeCoreWireTest {
    @Test
    fun runtimeOperationDecodesReadySelection() {
        val operation = decodeReferenceRuntimeOperation(
            """{
              "selection": {
                "database_path": "/data/user/0/kr.yakbom.app/files/reference/mobile-deadbeef.sqlite",
                "unavailable_reason": null
              },
              "error": null
            }""".trimIndent(),
        )

        assertTrue(operation.selection?.referenceAvailable == true)
        assertTrue(operation.selection?.database?.path?.endsWith("mobile-deadbeef.sqlite") == true)
        assertNull(operation.error)
    }

    @Test
    fun runtimeOperationDecodesUnavailableSelectionAndDiagnostic() {
        val operation = decodeReferenceRuntimeOperation(
            """{
              "selection": {
                "database_path": null,
                "unavailable_reason": "update_required"
              },
              "error": "manifest_release: unsupported contract"
            }""".trimIndent(),
        )

        assertEquals(false, operation.selection?.referenceAvailable)
        assertEquals("update_required", operation.selection?.referenceUnavailableReason)
        assertEquals("manifest_release: unsupported contract", operation.error)
    }
}
