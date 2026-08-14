(function attachBrowserOcrParser(root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MedicineBrowserOcrParser = api;
})(typeof window === "object" ? window : globalThis, function createBrowserOcrParser() {
  "use strict";

  const PRODUCT_KO = /(?:약명|제품명)\s*[:：]\s*([가-힣][가-힣0-9-]*(?:정|캡슐|시럽)?)/giu;
  const PRODUCT_EN = /(?:product|medicine)\s*[:：]\s*([A-Za-z][A-Za-z0-9-]*)/giu;
  const DOSE = /(?:(\d+(?:\.\d+)?)|(\d+)\s*\/\s*(\d+))\s*(정|캡슐|포|tablet|capsule|mL|㎖)/giu;
  const LABELED_FREQUENCY = /(?:1\s*일\s*)?복용\s*횟수\s*[:：]\s*(\d+)\s*회/iu;
  const FREQUENCY = /(?:\d+\s*일\s*)?(\d+)\s*(?:회(?:\s*\/\s*일)?|times?\s*\/\s*day)/iu;
  const LABELED_DURATION = /(?:총\s*)?복용\s*일수\s*[:：]\s*(\d+)\s*일/iu;
  const DURATION = /(?:for\s*)?(\d+)\s*(?:일(?!\s*\d*\s*회)|days?)/iu;
  const TIME_KO = /(오전|오후)\s*(\d{1,2})(?:\s*시)?(?:\s*[:：]\s*(\d{2}))?/giu;
  const TIME_EN = /\b(AM|PM)\s*(\d{1,2})(?:\s*[:：]\s*(\d{2}))?\b/giu;
  const TIME_24 = /(?:^|[^0-9])([01]?[0-9]|2[0-3])\s*[:：]\s*([0-5][0-9])(?![0-9])/gu;
  // This is a catastrophic-confidence safety floor, not a calibrated OCR quality threshold.
  // Broader confidence calibration belongs to the independent model evaluation pipeline.
  const STRUCTURED_SCORE_FLOOR = 0.05;

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
    for (const line of String(value || "").split(/\r?\n/u)) {
      if (!/(?:복용|투약|투여|복약)\s*시간|schedule\s*times?/iu.test(line)) continue;
      TIME_24.lastIndex = 0;
      for (const match of line.matchAll(TIME_24)) {
        found.push(`${String(Number(match[1])).padStart(2, "0")}:${match[2]}`);
      }
    }
    return distinct(found.filter(Boolean)).sort();
  }

  function parsedDose(value) {
    const text = String(value || "");
    DOSE.lastIndex = 0;
    for (const match of text.matchAll(DOSE)) {
      const start = match.index || 0;
      const before = text.slice(0, start);
      const after = text.slice(start + match[0].length);
      if (/[0-9][ ,.~～–—-]$/u.test(before) || /[~～–—-]\s*[0-9]/u.test(after)) continue;
      const rawUnit = match[4] || "";
      if (/^(?:ml|㎖)$/iu.test(rawUnit) && /(?:mg|mcg|µg|μg|g)\s*\/\s*$/iu.test(before)) continue;
      let amount = null;
      if (match[1] !== undefined) amount = Number(match[1]);
      else {
        const numerator = Number(match[2]);
        const denominator = Number(match[3]);
        if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) continue;
        amount = numerator / denominator;
      }
      if (!Number.isFinite(amount) || amount <= 0) continue;
      const unit = rawUnit.toLowerCase();
      return {
        amount,
        unit: unit === "tablet" ? "정" : unit === "capsule" ? "캡슐" : /^(?:ml|㎖)$/u.test(unit) ? "mL" : unit || null,
      };
    }
    return null;
  }

  function parsePrescriptionHints(value) {
    const ephemeral = typeof value === "string" ? value : "";
    const products = distinct([...matches(PRODUCT_KO, ephemeral), ...matches(PRODUCT_EN, ephemeral)]).slice(0, 12);
    const dose = parsedDose(ephemeral);
    const scalarText = ephemeral
      .replace(/1\s*회\s*(?:(?:복용|투여|투약)\s*)?(?:량|용량)/giu, " ")
      .replace(/1\s*일\s*(?:(?:복용|투여|투약)\s*)?(?:횟수|회수|량|용량)/giu, " ");
    const frequencyRange = /(?:1\s*일|하루)?\s*\d+\s*[~～–—-]\s*\d+\s*회/iu.test(scalarText);
    const durationRange = /\d+\s*[~～–—-]\s*\d+\s*(?:일|days?)/iu.test(scalarText);
    const durationText = scalarText.replace(/(?:1\s*일|하루)\s*\d+(?:\s*[~～–—-]\s*\d+)?\s*회/giu, " ");
    // Medication-bag row headings contain "1일"/"1회" before the actual value.
    // Prefer explicit labels so those structural numbers cannot become dosage hints.
    const frequencyMatch = frequencyRange ? null : (ephemeral.match(LABELED_FREQUENCY) || scalarText.match(FREQUENCY));
    const durationMatch = durationRange ? null : (ephemeral.match(LABELED_DURATION) || durationText.match(DURATION));
    const ambiguityCodes = [];
    const unsupportedCodes = [];
    if (!products.length) ambiguityCodes.push("MISSING_PRODUCT");
    if (products.length > 1) ambiguityCodes.push("AMBIGUOUS_PRODUCT");
    if (/필요시|\bPRN\b|as\s+needed/iu.test(ephemeral)) unsupportedCodes.push("UNSUPPORTED_AS_NEEDED");
    if (/주사|inject(?:ion)?/iu.test(ephemeral)) unsupportedCodes.push("UNSUPPORTED_ROUTE");
    return {
      product_queries: products,
      dose_quantity: dose?.amount ?? null,
      dose_unit: dose?.unit ?? null,
      frequency_per_day: frequencyMatch ? Number(frequencyMatch[1]) : null,
      duration_days: durationMatch ? Number(durationMatch[1]) : null,
      times: parsedTimes(ephemeral),
      ambiguity_codes: ambiguityCodes,
      unsupported_codes: unsupportedCodes,
    };
  }

  function rowTemplate(productQuery, association) {
    return {
      product_query: productQuery || "",
      dose_amount: null,
      dose_unit: null,
      frequency_per_day: null,
      prescription_days: null,
      schedule_times: [],
      meal_relation: null,
      administration_route: null,
      as_needed: false,
      association,
    };
  }

  function mealRelation(value) {
    if (/식사\s*(?:와|와 함께|중)|식중/iu.test(value)) return "with_meal";
    if (/식후|식사\s*후/iu.test(value)) return "after_meal";
    if (/식전|식사\s*전/iu.test(value)) return "before_meal";
    if (/공복|빈속/iu.test(value)) return "empty_stomach";
    if (/식사\s*(?:무관|관계\s*없이)/iu.test(value)) return "regardless";
    return null;
  }

  function administrationRoute(value) {
    if (/점안|안약|ophthalm/iu.test(value)) return "ophthalmic";
    if (/점이|귀에|otic/iu.test(value)) return "otic";
    if (/점비|비강|nasal/iu.test(value)) return "nasal";
    if (/흡입|inhal/iu.test(value)) return "inhaled";
    if (/주사|inject/iu.test(value)) return "injection";
    if (/외용|도포|바르|연고|크림|겔|로션|topical/iu.test(value)) return "topical";
    if (/경구|복용|먹|oral/iu.test(value)) return "oral";
    return null;
  }

  function regimenFields(value) {
    const hints = parsePrescriptionHints(value);
    const text = String(value || "");
    const labeledFrequency = text.match(/(?:1\s*일\s*)?(?:복용|투여|투약)\s*횟수\s*[:：]\s*(\d+)\s*회/iu);
    const dailyFrequency = text.match(/(?:1\s*일|하루)\s*(\d+)\s*회|(?:\d+\s*회\s*\/\s*일)/iu);
    const labeledDuration = text.match(/(?:총\s*)?(?:복용|투여|투약)\s*일수\s*[:：]\s*(\d+)\s*일/iu);
    let frequency = labeledFrequency ? Number(labeledFrequency[1])
      : dailyFrequency?.[1] ? Number(dailyFrequency[1]) : hints.frequency_per_day;
    let duration = labeledDuration ? Number(labeledDuration[1]) : hints.duration_days;
    if (/1\s*회\s*(?:복용|투여|투약)\s*(?:량|용량)/iu.test(text) && !labeledFrequency && !dailyFrequency) frequency = null;
    if (/1\s*일\s*(?:복용|투여|투약)\s*횟수/iu.test(text) && !labeledDuration) duration = null;
    return {
      dose_amount: hints.dose_quantity,
      dose_unit: hints.dose_unit,
      frequency_per_day: frequency,
      prescription_days: duration,
      schedule_times: hints.times,
      meal_relation: mealRelation(value),
      administration_route: administrationRoute(value),
      as_needed: /필요시|필요\s*시|\bPRN\b|as\s+needed/iu.test(value),
    };
  }

  function applyFields(row, fields, overwrite = false) {
    let changed = false;
    for (const key of ["dose_amount", "dose_unit", "frequency_per_day", "prescription_days", "meal_relation", "administration_route"]) {
      if (fields[key] !== null && fields[key] !== undefined && (overwrite || row[key] === null || row[key] === undefined)) {
        row[key] = fields[key];
        changed = true;
      }
    }
    if (Array.isArray(fields.schedule_times) && fields.schedule_times.length && (overwrite || !row.schedule_times.length)) {
      row.schedule_times = fields.schedule_times.slice();
      changed = true;
    }
    if (fields.as_needed && (overwrite || !row.as_needed)) {
      row.as_needed = true;
      changed = true;
    }
    return changed;
  }

  function geometry(item) {
    if (!item || typeof item.text !== "string" || !Array.isArray(item.poly) || item.poly.length < 4) return null;
    const points = item.poly.slice(0, 4).map((point) => [Number(point?.[0]), Number(point?.[1])]);
    if (points.some((point) => !Number.isFinite(point[0]) || !Number.isFinite(point[1]))) return null;
    const xs = points.map((point) => point[0]);
    const ys = points.map((point) => point[1]);
    const x1 = Math.min(...xs), x2 = Math.max(...xs), y1 = Math.min(...ys), y2 = Math.max(...ys);
    if (!(x2 > x1) || !(y2 > y1)) return null;
    const rawScore = Number(item.score);
    const score = Number.isFinite(rawScore) ? Math.max(0, Math.min(1, rawScore)) : null;
    return { text: item.text.trim(), score, points, x1, x2, y1, y2, cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, h: y2 - y1 };
  }

  function estimatedPageSlope(tokens) {
    const slopes = [];
    for (const token of tokens) {
      for (const [leftIndex, rightIndex] of [[0, 1], [3, 2]]) {
        const left = token.points[leftIndex];
        const right = token.points[rightIndex];
        const dx = right[0] - left[0];
        if (Math.abs(dx) < 1) continue;
        const slope = (right[1] - left[1]) / dx;
        if (Number.isFinite(slope) && Math.abs(slope) <= 0.35) slopes.push(slope);
      }
    }
    if (slopes.length < 2) return 0;
    slopes.sort((a, b) => a - b);
    return slopes[Math.floor(slopes.length / 2)];
  }

  function documentLines(items) {
    const allTokens = (Array.isArray(items) ? items : []).map(geometry).filter((item) => item?.text);
    const lowConfidence = allTokens.some((token) => token.score !== null && token.score < STRUCTURED_SCORE_FLOOR);
    const rawTokens = allTokens.filter((token) => token.score === null || token.score >= STRUCTURED_SCORE_FLOOR);
    const pageSlope = estimatedPageSlope(rawTokens);
    const tokens = rawTokens.map((token) => {
      const deskewedYs = token.points.map((point) => point[1] - pageSlope * point[0]);
      const y1 = Math.min(...deskewedYs);
      const y2 = Math.max(...deskewedYs);
      return { ...token, y1, y2, cy: (y1 + y2) / 2, h: y2 - y1 };
    });
    tokens.sort((a, b) => a.cy - b.cy || a.x1 - b.x1);
    const heights = tokens.map((item) => item.h).sort((a, b) => a - b);
    const medianHeight = heights.length ? heights[Math.floor(heights.length / 2)] : 20;
    const lines = [];
    for (const token of tokens) {
      let best = null;
      for (const line of lines) {
        if (Math.abs(line.cy - token.cy) <= Math.max(medianHeight * 0.65, Math.min(line.h, token.h) * 0.7)) {
          best = line;
          break;
        }
      }
      if (!best) {
        best = { items: [], cy: token.cy, h: token.h };
        lines.push(best);
      }
      best.items.push(token);
      best.items.sort((a, b) => a.x1 - b.x1);
      best.cy = best.items.reduce((sum, item) => sum + item.cy, 0) / best.items.length;
      best.h = Math.max(...best.items.map((item) => item.h));
    }
    lines.sort((a, b) => a.cy - b.cy);
    for (const line of lines) line.text = line.items.map((item) => item.text).join(" ");
    return { lines, medianHeight, lowConfidence };
  }

  function headerKey(value) {
    const text = String(value || "").replaceAll(" ", "");
    if (/^(?:약품명|의약품명|제품명|처방의약품명|약명)$/u.test(text)) return "product";
    if (/1회.*(?:투약|투여|복용).*(?:량|용량)|1회량/u.test(text)) return "dose";
    if (/1일.*(?:투약|투여|복용).*(?:횟수|회수)|1일횟수/u.test(text)) return "frequency";
    if (/총.*(?:투약|투여|복용).*일수|(?:투약|투여|복용)일수/u.test(text)) return "days";
    return null;
  }

  function headerAnchors(line) {
    const items = line.items || [];
    const anchors = [];
    for (let index = 0; index < items.length;) {
      let best = null;
      for (let width = 1; width <= 3 && index + width <= items.length; width += 1) {
        const group = items.slice(index, index + width);
        const key = headerKey(group.map((item) => item.text).join(""));
        if (key && !best) best = { key, x: (group[0].x1 + group[group.length - 1].x2) / 2, width };
      }
      if (best) {
        anchors.push({ key: best.key, x: best.x });
        index += best.width;
      } else {
        index += 1;
      }
    }
    return anchors;
  }

  function cleanProduct(value) {
    return String(value || "")
      .replace(/^(?:약명|제품명|약품명|의약품명)\s*[:：]\s*/iu, "")
      .replace(/^\s*[0-9]+[.)]\s*/, "")
      .trim();
  }

  function tableCellFields(key, value) {
    const fields = regimenFields(value);
    const text = String(value || "").trim();
    const numeric = text.match(/^([0-9]+(?:\.[0-9]+)?)$/u);
    const fraction = text.match(/^([0-9]+)\s*\/\s*([0-9]+)$/u);
    let amount = numeric ? Number(numeric[1]) : null;
    if (fraction) {
      const denominator = Number(fraction[2]);
      amount = denominator > 0 ? Number(fraction[1]) / denominator : null;
    }
    if (!Number.isFinite(amount) || amount <= 0) return fields;
    if (key === "dose") fields.dose_amount = amount;
    else if (key === "frequency" && Number.isInteger(amount)) fields.frequency_per_day = amount;
    else if (key === "days" && Number.isInteger(amount)) fields.prescription_days = amount;
    return fields;
  }

  function tableRows(lines) {
    for (let headerIndex = 0; headerIndex < lines.length; headerIndex += 1) {
      const header = lines[headerIndex];
      const anchors = headerAnchors(header);
      if (!anchors.some((item) => item.key === "product") || new Set(anchors.map((item) => item.key)).size < 3) continue;
      anchors.sort((a, b) => a.x - b.x);
      const boundaries = anchors.slice(0, -1).map((item, index) => (item.x + anchors[index + 1].x) / 2);
      const result = [];
      for (let index = headerIndex + 1; index < lines.length; index += 1) {
        const line = lines[index];
        if (line.items.some((item) => headerKey(item.text))) break;
        const cells = new Map();
        for (const item of line.items) {
          let column = boundaries.findIndex((boundary) => item.cx < boundary);
          if (column < 0) column = anchors.length - 1;
          const key = anchors[column]?.key;
          if (!key) continue;
          cells.set(key, `${cells.get(key) || ""} ${item.text}`.trim());
        }
        const product = cleanProduct(cells.get("product"));
        const hasRegimenCell = ["dose", "frequency", "days"].some((key) => cells.has(key));
        if (!product || !hasRegimenCell) continue;
        const row = rowTemplate(product, "table_row");
        if (cells.get("dose")) applyFields(row, tableCellFields("dose", cells.get("dose")));
        if (cells.get("frequency")) applyFields(row, tableCellFields("frequency", cells.get("frequency")));
        if (cells.get("days")) applyFields(row, tableCellFields("days", cells.get("days")));
        applyFields(row, regimenFields(line.text));
        result.push(row);
      }
      if (result.length) return result;
    }
    return [];
  }

  function labeledProduct(value) {
    const match = String(value || "").match(/(?:^|\s)(?:약명|제품명|약품명|의약품명)\s*[:：]\s*([^|]+)$/iu);
    return match ? cleanProduct(match[1]) : null;
  }

  function isCommonRegimen(value) {
    return /^공통\s*(?:복용법|용법|복약방법)\s*[:：]/iu.test(String(value || "").trim());
  }

  function labeledRows(lines, medianHeight) {
    const productLines = lines.map((line, index) => ({ index, product: labeledProduct(line.text) })).filter((item) => item.product);
    if (!productLines.length) return { rows: [], common: null, unassociatedRegimen: false };
    const commonLines = lines.map((line, index) => ({ index, line })).filter((item) => isCommonRegimen(item.line.text));
    const commonIndexes = new Set(commonLines.map((item) => item.index));
    const rows = [];
    const consumed = new Set();
    for (let markerIndex = 0; markerIndex < productLines.length; markerIndex += 1) {
      const marker = productLines[markerIndex];
      const nextProductIndex = productLines[markerIndex + 1]?.index ?? lines.length;
      const row = rowTemplate(marker.product, "labeled_block");
      consumed.add(marker.index);
      let previousCy = lines[marker.index].cy;
      for (let index = marker.index + 1; index < nextProductIndex; index += 1) {
        const line = lines[index];
        if (commonIndexes.has(index)) break;
        if (line.cy - previousCy > Math.max(55, medianHeight * 2.8)) break;
        previousCy = line.cy;
        const fields = regimenFields(line.text);
        const hasField = fields.dose_amount !== null || fields.frequency_per_day !== null
          || fields.prescription_days !== null || fields.schedule_times.length
          || fields.meal_relation !== null || fields.administration_route !== null || fields.as_needed;
        if (hasField) {
          applyFields(row, fields);
          consumed.add(index);
        }
      }
      rows.push(row);
    }
    // A shared instruction is safe to propagate only to the uninterrupted run of
    // medication labels immediately above it. Any intervening regimen/text line
    // or a medication declared after the common line is outside that proven group.
    let appliedCommon = null;
    for (const commonItem of commonLines) {
      const commonFields = regimenFields(commonItem.line.text.replace(/^[^:：]+[:：]\s*/u, ""));
      const memberIndexes = [];
      let cursor = commonItem.index - 1;
      let lowerCy = commonItem.line.cy;
      while (cursor >= 0) {
        const line = lines[cursor];
        if (lowerCy - line.cy > Math.max(55, medianHeight * 2.8)) break;
        const markerPosition = productLines.findIndex((item) => item.index === cursor);
        if (markerPosition < 0) break;
        memberIndexes.unshift(markerPosition);
        lowerCy = line.cy;
        cursor -= 1;
      }
      if (!memberIndexes.length) continue;
      let changed = false;
      for (const rowIndex of memberIndexes) {
        if (applyFields(rows[rowIndex], commonFields)) {
          rows[rowIndex].association = "group_shared";
          changed = true;
        }
      }
      if (changed) {
        appliedCommon = commonFields;
        consumed.add(commonItem.index);
      }
    }
    let unassociatedRegimen = false;
    if (rows.length > 1) {
      for (let index = 0; index < lines.length; index += 1) {
        if (consumed.has(index) || labeledProduct(lines[index].text)) continue;
        const fields = regimenFields(lines[index].text);
        if (fields.dose_amount !== null || fields.frequency_per_day !== null || fields.prescription_days !== null || fields.schedule_times.length) {
          unassociatedRegimen = true;
        }
      }
    }
    return { rows, common: appliedCommon, unassociatedRegimen };
  }

  function parsePrescriptionDocument(items) {
    const { lines, medianHeight, lowConfidence } = documentLines(items);
    const recognized = lines.length
      ? lines.map((line) => line.text).join("\n")
      : (Array.isArray(items) ? items : [])
        .filter((item) => {
          const score = Number(item?.score);
          return !Number.isFinite(score) || score >= STRUCTURED_SCORE_FLOOR;
        })
        .map((item) => typeof item?.text === "string" ? item.text : "")
        .filter(Boolean)
        .join("\n");
    const globalHints = parsePrescriptionHints(recognized);
    let rows = tableRows(lines);
    let unassociatedRegimen = false;
    if (!rows.length) {
      const labeled = labeledRows(lines, medianHeight);
      rows = labeled.rows;
      unassociatedRegimen = labeled.unassociatedRegimen;
    }
    if (!rows.length) {
      if (globalHints.product_queries.length === 1) {
        const row = rowTemplate(globalHints.product_queries[0], "single_document");
        applyFields(row, regimenFields(recognized));
        rows = [row];
      } else if (globalHints.product_queries.length > 1) {
        rows = globalHints.product_queries.map((product) => rowTemplate(product, "unresolved"));
        unassociatedRegimen = true;
      }
    }
    const ambiguity = [];
    if (!rows.length) ambiguity.push("MISSING_PRODUCT");
    if (unassociatedRegimen) ambiguity.push("UNRESOLVED_REGIMEN_ASSOCIATION");
    if (lowConfidence) ambiguity.push("LOW_CONFIDENCE_OCR");
    return {
      rows: rows.slice(0, 24),
      product_queries: distinct(rows.map((row) => row.product_query)).slice(0, 24),
      ambiguity_codes: ambiguity,
      unsupported_codes: globalHints.unsupported_codes || [],
    };
  }

  return { parsePrescriptionHints, parsePrescriptionDocument };
});
