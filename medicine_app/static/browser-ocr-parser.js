(function attachBrowserOcrParser(root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MedicineBrowserOcrParser = api;
})(typeof window === "object" ? window : globalThis, function createBrowserOcrParser() {
  "use strict";

  const PRODUCT_KO = /(?:약명|제품명)\s*[:：]\s*([가-힣][가-힣0-9-]*(?:정|캡슐|시럽)?)/giu;
  const PRODUCT_EN = /(?:product|medicine)\s*[:：]\s*([A-Za-z][A-Za-z0-9-]*)/giu;
  const DOSE = /(\d+)\s*(정|캡슐|포|tablet|capsule)/iu;
  const LABELED_FREQUENCY = /(?:1\s*일\s*)?복용\s*횟수\s*[:：]\s*(\d+)\s*회/iu;
  const FREQUENCY = /(?:\d+\s*일\s*)?(\d+)\s*(?:회(?:\s*\/\s*일)?|times?\s*\/\s*day)/iu;
  const LABELED_DURATION = /(?:총\s*)?복용\s*일수\s*[:：]\s*(\d+)\s*일/iu;
  const DURATION = /(?:for\s*)?(\d+)\s*(?:일(?!\s*\d*\s*회)|days?)/iu;
  const TIME_KO = /(오전|오후)\s*(\d{1,2})(?:\s*시)?(?:\s*[:：]\s*(\d{2}))?/giu;
  const TIME_EN = /\b(AM|PM)\s*(\d{1,2})(?:\s*[:：]\s*(\d{2}))?\b/giu;

  function distinct(values) {
    return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
  }

  function matches(expression, value, group = 1) {
    expression.lastIndex = 0;
    return [...value.matchAll(expression)].map((match) => match[group]);
  }

  function normalizedTime(period, hourValue, minuteValue) {
    let hour = Number(hourValue);
    const minute = minuteValue ? Number(minuteValue) : 0;
    if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour < 1 || hour > 12 || minute < 0 || minute > 59) return null;
    if (/^(오후|PM)$/iu.test(period)) {
      if (hour < 12) hour += 12;
    } else if (hour === 12) hour = 0;
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }

  function parsedTimes(value) {
    const found = [];
    TIME_KO.lastIndex = 0;
    for (const match of value.matchAll(TIME_KO)) found.push(normalizedTime(match[1], match[2], match[3]));
    TIME_EN.lastIndex = 0;
    for (const match of value.matchAll(TIME_EN)) found.push(normalizedTime(match[1], match[2], match[3]));
    return distinct(found.filter(Boolean)).sort();
  }

  function parsePrescriptionHints(value) {
    const ephemeral = typeof value === "string" ? value : "";
    const products = distinct([...matches(PRODUCT_KO, ephemeral), ...matches(PRODUCT_EN, ephemeral)]).slice(0, 12);
    const doseMatch = ephemeral.match(DOSE);
    // Medication-bag row headings contain "1일"/"1회" before the actual value.
    // Prefer explicit labels so those structural numbers cannot become dosage hints.
    const frequencyMatch = ephemeral.match(LABELED_FREQUENCY) || ephemeral.match(FREQUENCY);
    const durationMatch = ephemeral.match(LABELED_DURATION) || ephemeral.match(DURATION);
    const unit = doseMatch?.[2]?.toLowerCase();
    const ambiguityCodes = [];
    const unsupportedCodes = [];
    if (!products.length) ambiguityCodes.push("MISSING_PRODUCT");
    if (products.length > 1) ambiguityCodes.push("AMBIGUOUS_PRODUCT");
    if (/필요시|\bPRN\b|as\s+needed/iu.test(ephemeral)) unsupportedCodes.push("UNSUPPORTED_AS_NEEDED");
    if (/주사|inject(?:ion)?/iu.test(ephemeral)) unsupportedCodes.push("UNSUPPORTED_ROUTE");
    return {
      product_queries: products,
      dose_quantity: doseMatch ? Number(doseMatch[1]) : null,
      dose_unit: unit === "tablet" ? "정" : unit === "capsule" ? "캡슐" : unit || null,
      frequency_per_day: frequencyMatch ? Number(frequencyMatch[1]) : null,
      duration_days: durationMatch ? Number(durationMatch[1]) : null,
      times: parsedTimes(ephemeral),
      ambiguity_codes: ambiguityCodes,
      unsupported_codes: unsupportedCodes,
    };
  }

  return { parsePrescriptionHints };
});
