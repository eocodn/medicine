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
    const xs = item.poly.map((point) => Number(point?.[0])).filter(Number.isFinite);
    const ys = item.poly.map((point) => Number(point?.[1])).filter(Number.isFinite);
    if (xs.length < 4 || ys.length < 4) return null;
    const x1 = Math.min(...xs), x2 = Math.max(...xs), y1 = Math.min(...ys), y2 = Math.max(...ys);
    if (!(x2 > x1) || !(y2 > y1)) return null;
    return { text: item.text.trim(), x1, x2, y1, y2, cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, h: y2 - y1 };
  }

  function documentLines(items) {
    const tokens = (Array.isArray(items) ? items : []).map(geometry).filter((item) => item?.text);
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
    return { lines, medianHeight };
  }

  function headerKey(value) {
    const text = String(value || "").replaceAll(" ", "");
    if (/^(?:약품명|의약품명|제품명|처방의약품명|약명)$/u.test(text)) return "product";
    if (/1회.*(?:투약|투여|복용).*(?:량|용량)|1회량/u.test(text)) return "dose";
    if (/1일.*(?:투약|투여|복용).*(?:횟수|회수)|1일횟수/u.test(text)) return "frequency";
    if (/총.*(?:투약|투여|복용).*일수|(?:투약|투여|복용)일수/u.test(text)) return "days";
    return null;
  }

  function cleanProduct(value) {
    return String(value || "")
      .replace(/^(?:약명|제품명|약품명|의약품명)\s*[:：]\s*/iu, "")
      .replace(/^\s*[0-9]+[.)]\s*/, "")
      .trim();
  }

  function tableCellFields(key, value) {
    const fields = regimenFields(value);
    const numeric = String(value || "").trim().match(/^([0-9]+(?:\.[0-9]+)?)$/u);
    if (!numeric) return fields;
    const amount = Number(numeric[1]);
    if (key === "dose") fields.dose_amount = amount;
    else if (key === "frequency") fields.frequency_per_day = amount;
    else if (key === "days") fields.prescription_days = amount;
    return fields;
  }

  function tableRows(lines) {
    for (let headerIndex = 0; headerIndex < lines.length; headerIndex += 1) {
      const header = lines[headerIndex];
      const anchors = header.items.map((item) => ({ key: headerKey(item.text), x: item.cx })).filter((item) => item.key);
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
    return /^(?:공통\s*)?(?:복용법|용법|복약방법)\s*[:：]/iu.test(String(value || "").trim());
  }

  function labeledRows(lines, medianHeight) {
    const productLines = lines.map((line, index) => ({ index, product: labeledProduct(line.text) })).filter((item) => item.product);
    if (!productLines.length) return { rows: [], common: null, unassociatedRegimen: false };
    const explicitCommon = lines.find((line) => /^(?:공통\s+)(?:복용법|용법|복약방법)\s*[:：]/iu.test(line.text.trim())) || null;
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
        if (explicitCommon === line || isCommonRegimen(line.text)) break;
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
    let common = null;
    if (explicitCommon) common = regimenFields(explicitCommon.text.replace(/^[^:：]+[:：]\s*/u, ""));
    if (common) {
      for (const row of rows) {
        if (applyFields(row, common)) row.association = "group_shared";
      }
    }
    let unassociatedRegimen = false;
    if (rows.length > 1) {
      for (let index = 0; index < lines.length; index += 1) {
        if (consumed.has(index) || lines[index] === explicitCommon || labeledProduct(lines[index].text)) continue;
        const fields = regimenFields(lines[index].text);
        if (fields.dose_amount !== null || fields.frequency_per_day !== null || fields.prescription_days !== null || fields.schedule_times.length) {
          unassociatedRegimen = true;
        }
      }
    }
    return { rows, common, unassociatedRegimen };
  }

  function parsePrescriptionDocument(items) {
    const { lines, medianHeight } = documentLines(items);
    const recognized = lines.length
      ? lines.map((line) => line.text).join("\n")
      : (Array.isArray(items) ? items : []).map((item) => typeof item?.text === "string" ? item.text : "").filter(Boolean).join("\n");
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
    return {
      rows: rows.slice(0, 24),
      product_queries: distinct(rows.map((row) => row.product_query)).slice(0, 24),
      ambiguity_codes: ambiguity,
      unsupported_codes: globalHints.unsupported_codes || [],
    };
  }

  return { parsePrescriptionHints, parsePrescriptionDocument };
});
