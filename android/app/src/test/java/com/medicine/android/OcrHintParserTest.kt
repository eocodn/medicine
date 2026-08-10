package com.medicine.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OcrHintParserTest {
    @Test
    fun koreanPrescriptionProducesStructuredHintsWithoutRawText() {
        val hints = OcrHintParser.parse(fixture("korean-prescription.txt"))

        assertEquals(listOf("타이레놀정"), hints.productQueries)
        assertEquals(1, hints.doseQuantity)
        assertEquals("정", hints.doseUnit)
        assertEquals(2, hints.frequencyPerDay)
        assertEquals(7, hints.durationDays)
        assertEquals(listOf("08:00", "14:00"), hints.times)
        assertTrue(hints.toString().contains("productQueries"))
        assertTrue(!hints.toString().contains("오전 8시"))
    }

    @Test
    fun latinProductAndAfternoonTimeAreNormalized() {
        val hints = OcrHintParser.parse(fixture("latin-prescription.txt"))

        assertEquals(listOf("amoxicillin"), hints.productQueries)
        assertEquals(1, hints.doseQuantity)
        assertEquals("캡슐", hints.doseUnit)
        assertEquals(2, hints.frequencyPerDay)
        assertEquals(7, hints.durationDays)
        assertEquals(listOf("14:30"), hints.times)
    }

    @Test
    fun ambiguityAndUnsupportedInstructionsAreExplicit() {
        val hints = OcrHintParser.parse(fixture("ambiguous-prescription.txt"))

        assertEquals(listOf("타이레놀정", "이부프로펜정"), hints.productQueries)
        assertTrue("AMBIGUOUS_PRODUCT" in hints.ambiguityCodes)
        assertTrue("UNSUPPORTED_AS_NEEDED" in hints.unsupportedCodes)
        assertTrue("UNSUPPORTED_ROUTE" in hints.unsupportedCodes)
    }

    @Test
    fun unsupportedOrMissingMedicineNeverBecomesAProductQuery() {
        val hints = OcrHintParser.parse("환자용 안내문\n혈압을 확인하세요\n1일 3회")

        assertTrue(hints.productQueries.isEmpty())
        assertTrue("MISSING_PRODUCT" in hints.ambiguityCodes)
        assertEquals(3, hints.frequencyPerDay)
    }
    private fun fixture(name: String): String =
        checkNotNull(javaClass.classLoader?.getResourceAsStream("golden/$name"))
            .bufferedReader()
            .use { it.readText() }
}
