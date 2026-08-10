package com.medicine.android

/**
 * The parser intentionally accepts OCR text only as an ephemeral argument and
 * returns a closed set of editable hints. It has no serializer or field that
 * can carry source OCR, image bytes, content URIs, or local paths.
 */
data class OcrHints(
    val schemaVersion: Int = 1,
    val productQueries: List<String> = emptyList(),
    val doseQuantity: Int? = null,
    val doseUnit: String? = null,
    val frequencyPerDay: Int? = null,
    val durationDays: Int? = null,
    val times: List<String> = emptyList(),
    val ambiguityCodes: List<String> = emptyList(),
    val unsupportedCodes: List<String> = emptyList()
)

object OcrHintParser {
    private val koreanProduct = Regex("(?:약명|제품명)\\s*[:：]\\s*([가-힣][가-힣0-9-]*(?:정|캡슐|시럽)?)")
    private val latinProduct = Regex("(?:product|medicine)\\s*[:：]\\s*([A-Za-z][A-Za-z0-9-]*)", RegexOption.IGNORE_CASE)
    private val dose = Regex("(\\d+)\\s*(정|캡슐|포|tablet|capsule)", RegexOption.IGNORE_CASE)
    private val frequency = Regex("(?:\\d+\\s*일\\s*)?(\\d+)\\s*(?:회(?:\\s*/\\s*일)?|times?\\s*/\\s*day)", RegexOption.IGNORE_CASE)
    private val duration = Regex("(?:for\\s*)?(\\d+)\\s*(?:일(?!\\s*\\d*\\s*회)|days?)", RegexOption.IGNORE_CASE)
    private val koreanTime = Regex("(오전|오후)\\s*(\\d{1,2})(?:\\s*시)?(?:\\s*[:：]\\s*(\\d{2}))?")
    private val latinTime = Regex("\\b(AM|PM)\\s*(\\d{1,2})(?:\\s*[:：]\\s*(\\d{2}))?\\b", RegexOption.IGNORE_CASE)

    fun parse(ocrText: String): OcrHints {
        val products = (koreanProduct.findAll(ocrText).map { it.groupValues[1] } +
            latinProduct.findAll(ocrText).map { it.groupValues[1] })
            .map(String::trim)
            .filter(String::isNotEmpty)
            .distinct()
            .toList()

        val doseMatch = dose.find(ocrText)
        val quantity = doseMatch?.groupValues?.get(1)?.toIntOrNull()
        val unit = doseMatch?.groupValues?.get(2)?.lowercase()?.let {
            when (it) {
                "tablet" -> "정"
                "capsule" -> "캡슐"
                else -> it
            }
        }
        val perDay = frequency.find(ocrText)?.groupValues?.get(1)?.toIntOrNull()
        val days = duration.find(ocrText)?.groupValues?.get(1)?.toIntOrNull()
        val times = (koreanTime.findAll(ocrText).map { toTime(it.groupValues[1], it.groupValues[2], it.groupValues.getOrNull(3)) } +
            latinTime.findAll(ocrText).map { toTime(it.groupValues[1], it.groupValues[2], it.groupValues.getOrNull(3)) })
            .distinct()
            .sorted()
            .toList()

        val ambiguities = buildList {
            if (products.isEmpty()) add("MISSING_PRODUCT")
            if (products.size > 1) add("AMBIGUOUS_PRODUCT")
        }
        val unsupported = buildList {
            if (Regex("필요시|\\bPRN\\b|as\\s+needed", RegexOption.IGNORE_CASE).containsMatchIn(ocrText)) {
                add("UNSUPPORTED_AS_NEEDED")
            }
            if (Regex("주사|inject(?:ion)?", RegexOption.IGNORE_CASE).containsMatchIn(ocrText)) {
                add("UNSUPPORTED_ROUTE")
            }
        }
        return OcrHints(
            productQueries = products,
            doseQuantity = quantity,
            doseUnit = unit,
            frequencyPerDay = perDay,
            durationDays = days,
            times = times,
            ambiguityCodes = ambiguities,
            unsupportedCodes = unsupported
        )
    }

    private fun toTime(period: String, hourText: String, minuteText: String?): String {
        var hour = hourText.toInt()
        val minute = minuteText?.toIntOrNull() ?: 0
        if (period.equals("오후", ignoreCase = true) || period.equals("PM", ignoreCase = true)) {
            if (hour < 12) hour += 12
        } else if (hour == 12) {
            hour = 0
        }
        return "%02d:%02d".format(hour, minute)
    }
}
