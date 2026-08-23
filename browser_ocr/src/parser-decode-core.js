"use strict";

const { ROLE_LABELS } = require("./parser-graph-core.js");

const FIELD_ROLES = new Set(["dose", "frequency", "duration", "instruction", "schedule"]);
const PRODUCT_PREFIX = /^(?:약명|제품명|약품명|의약품명)\s*[:：]?\s*/i;
const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
const MEAL_RELATIONS = new Set(["before_meal", "after_meal", "with_meal", "empty_stomach", "regardless"]);
const ROUTES = new Set(["oral", "topical", "inhaled", "ophthalmic", "otic", "nasal", "injection"]);

function lexicalCompare(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

function sameValue(left, right) {
  return Array.isArray(left) || Array.isArray(right)
    ? JSON.stringify(left) === JSON.stringify(right)
    : left === right;
}

function rankedRole(scores) {
  if (!scores || typeof scores !== "object" || Array.isArray(scores)
      || Object.keys(scores).length !== ROLE_LABELS.length
      || ROLE_LABELS.some((role) => !Object.hasOwn(scores, role))) {
    throw new Error("parser role scores must contain exactly the graph role labels");
  }
  const ranked = ROLE_LABELS.map((role) => {
    const score = Number(scores[role]);
    if (!Number.isFinite(score) || score < 0 || score > 1) throw new Error("parser role scores must be finite probabilities");
    return { role, score };
  }).sort((left, right) => right.score - left.score || -lexicalCompare(left.role, right.role));
  return { role: ranked[0].role, score: ranked[0].score, second: ranked[1].score };
}

function decodeCandidates(graph, roleScores, config) {
  const nodeIds = graph.nodeIds.slice(1);
  if (!roleScores || typeof roleScores !== "object" || Array.isArray(roleScores)
      || Object.keys(roleScores).length !== nodeIds.length
      || nodeIds.some((nodeId) => !Object.hasOwn(roleScores, nodeId))) {
    throw new Error("parser role scores must cover every OCR node exactly once");
  }
  const predicted = Object.fromEntries(nodeIds.map((nodeId) => [nodeId, rankedRole(roleScores[nodeId])]));
  const products = nodeIds.filter((nodeId) => {
    const item = predicted[nodeId];
    return item.role === "product"
      && item.score >= config.product_threshold
      && item.score - item.second >= config.product_margin;
  });
  const fields = nodeIds.flatMap((nodeId) => {
    const item = predicted[nodeId];
    return FIELD_ROLES.has(item.role)
      && item.score >= config.field_threshold
      && item.score - item.second >= config.field_margin
      ? [[nodeId, item.role]] : [];
  });
  return { products, fields };
}

function parseNumber(raw) {
  if (raw.includes("/")) {
    const [numerator, denominator] = raw.split("/", 2).map(Number);
    if (!denominator) return null;
    return numerator / denominator;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function doseUnit(raw) {
  const lowered = raw.toLowerCase();
  if (lowered === "정" || lowered === "tablet") return "tablet";
  if (lowered === "캡슐" || lowered === "capsule") return "capsule";
  if (lowered === "포") return "packet";
  if (lowered === "ml") return "mL";
  return raw;
}

function doseValues(text) {
  const compact = String(text || "").replace(/\s+/gu, "");
  const packetTablet = compact.match(/(\d+(?:\.\d+)?|\d+\/\d+)포\(정\)/i);
  if (packetTablet) {
    const amount = parseNumber(packetTablet[1]);
    return amount == null ? {} : { dose_amount: amount, dosage_text: compact };
  }
  const match = compact.match(/(\d+(?:\.\d+)?|\d+\/\d+)\s*(정|캡슐|포|mL|ml|tablet|capsule)/i);
  if (!match) return {};
  const amount = parseNumber(match[1]);
  return amount == null ? {} : { dose_amount: amount, dose_unit: doseUnit(match[2]) };
}

function frequencyValues(text) {
  const match = String(text || "").replace(/\s+/gu, "").match(/(\d+)회/);
  if (!match) return {};
  const value = Number(match[1]);
  return Number.isInteger(value) && value >= 1 && value <= 24 ? { frequency_per_day: value } : {};
}

function durationValues(text) {
  const match = String(text || "").replace(/\s+/gu, "").match(/(\d+)일(?:분)?/);
  if (!match) return {};
  const value = Number(match[1]);
  return Number.isInteger(value) && value >= 1 && value <= 3650 ? { prescription_days: value } : {};
}

function normalizedTime(period, hourValue, minuteValue) {
  let hour = Number(hourValue);
  const minute = Number(minuteValue || "0");
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour < 1 || hour > 12 || minute < 0 || minute > 59) return null;
  if (String(period).toUpperCase() === "PM" || period === "오후") {
    if (hour < 12) hour += 12;
  } else if (hour === 12) {
    hour = 0;
  }
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function scheduleTimes(text) {
  const source = String(text || "");
  const found = new Set();
  for (const match of source.matchAll(/(오전|오후)\s*(\d{1,2})(?:\s*시)?(?:\s*[:：]\s*(\d{2}))?/gi)) {
    const value = normalizedTime(match[1], match[2], match[3]);
    if (value) found.add(value);
  }
  for (const match of source.matchAll(/\b(AM|PM)\s*(\d{1,2})(?:\s*[:：]\s*(\d{2}))?\b/gi)) {
    const value = normalizedTime(match[1], match[2], match[3]);
    if (value) found.add(value);
  }
  if (/(?:복용|투약|투여|복약)\s*시간|schedule\s*times?/i.test(source)) {
    for (const match of source.matchAll(/(?:^|[^0-9])([01]?[0-9]|2[0-3])\s*[:：]\s*([0-5][0-9])(?![0-9])/g)) {
      found.add(`${String(Number(match[1])).padStart(2, "0")}:${match[2]}`);
    }
  }
  return [...found].sort();
}

function instructionValues(text) {
  const source = String(text || "");
  const values = {};
  const meals = [];
  if (/식사\s*(?:와|와 함께|중)|식중/i.test(source)) meals.push("with_meal");
  if (/식후|식사\s*후/i.test(source)) meals.push("after_meal");
  if (/식전|식사\s*전/i.test(source)) meals.push("before_meal");
  if (/공복|빈속/i.test(source)) meals.push("empty_stomach");
  if (/식사\s*(?:무관|관계\s*없이)/i.test(source)) meals.push("regardless");
  if (new Set(meals).size === 1) values.meal_relation = meals[0];

  const routePatterns = [
    ["ophthalmic", /점안|안약|ophthalm/i],
    ["otic", /점이|귀에|otic/i],
    ["nasal", /점비|비강|nasal/i],
    ["inhaled", /흡입|inhal/i],
    ["injection", /주사|inject/i],
    ["topical", /외용|도포|바르|연고|크림|겔|로션|topical/i],
    ["oral", /경구|복용|먹|oral/i],
  ];
  const routes = routePatterns.filter(([, pattern]) => pattern.test(source)).map(([route]) => route);
  if (new Set(routes).size === 1) values.administration_route = routes[0];
  if (/필요\s*시|\bPRN\b|as\s+needed/i.test(source)) values.as_needed = true;
  const times = scheduleTimes(source);
  if (times.length) values.schedule_times = times;
  return values;
}

function typedValues(role, text) {
  if (role === "dose") return doseValues(text);
  if (role === "frequency") return frequencyValues(text);
  if (role === "duration") return durationValues(text);
  if (role === "instruction" || role === "schedule") return instructionValues(text);
  return {};
}

function normalizeDraft(raw) {
  const normalized = {};
  for (const [field, value] of Object.entries(raw)) {
    if (field === "dose_amount") {
      if (!Number.isFinite(value) || value <= 0) throw new Error("invalid dose_amount");
      normalized[field] = value;
    } else if (field === "frequency_per_day") {
      if (!Number.isInteger(value) || value < 1 || value > 24) throw new Error("invalid frequency_per_day");
      normalized[field] = value;
    } else if (field === "prescription_days") {
      if (!Number.isInteger(value) || value < 1 || value > 3650) throw new Error("invalid prescription_days");
      normalized[field] = value;
    } else if (field === "dose_unit") {
      if (typeof value !== "string" || !value.trim() || value.length > 64) throw new Error("invalid dose_unit");
      normalized[field] = value.trim();
    } else if (field === "dosage_text") {
      if (typeof value !== "string" || !value.trim() || value.length > 256) throw new Error("invalid dosage_text");
      normalized[field] = value.trim();
    } else if (field === "meal_relation") {
      if (!MEAL_RELATIONS.has(value)) throw new Error("invalid meal_relation");
      normalized[field] = value;
    } else if (field === "administration_route") {
      if (!ROUTES.has(value)) throw new Error("invalid administration_route");
      normalized[field] = value;
    } else if (field === "as_needed") {
      if (typeof value !== "boolean") throw new Error("invalid as_needed");
      normalized[field] = value;
    } else if (field === "schedule_times") {
      if (!Array.isArray(value) || value.length > 24 || value.some((item) => typeof item !== "string" || !TIME_RE.test(item))
          || new Set(value).size !== value.length) throw new Error("invalid schedule_times");
      normalized[field] = [...value];
    } else {
      throw new Error(`unsupported parser draft field: ${field}`);
    }
  }
  if (normalized.as_needed === true && (normalized.frequency_per_day != null || normalized.schedule_times?.length)) {
    throw new Error("PRN conflicts with fixed schedule");
  }
  if (normalized.schedule_times?.length && normalized.frequency_per_day != null
      && normalized.schedule_times.length !== normalized.frequency_per_day) {
    throw new Error("schedule count conflicts with frequency");
  }
  return normalized;
}

function addUncertainty(row, code) {
  if (!row.uncertainty_codes.includes(code)) row.uncertainty_codes.push(code);
}

function associationKey(productId, fieldId) {
  return `${productId}\0${fieldId}`;
}

function relationScoresFromLogits(pairs, logits) {
  if (!Array.isArray(pairs) || !logits || logits.length !== pairs.length) {
    throw new Error("parser relation logits shape does not match candidate pairs");
  }
  const scores = {};
  for (let index = 0; index < pairs.length; index += 1) {
    const [productId, fieldId] = pairs[index];
    const raw = Number(logits[index]);
    if (!Number.isFinite(raw)) throw new Error("parser relation logits must be finite");
    scores[associationKey(productId, fieldId)] = 1 / (1 + Math.exp(-raw));
  }
  return scores;
}

function decodeParserRows(graph, roleScores, associationScores, config) {
  const { products: rawProducts, fields } = decodeCandidates(graph, roleScores, config);
  const rows = new Map();
  for (const nodeId of rawProducts) {
    const node = graph.nodes[graph.nodeIndex.get(nodeId)];
    const productQuery = String(node.text || "").replace(PRODUCT_PREFIX, "").trim();
    if (!productQuery) continue;
    rows.set(nodeId, { row_id: nodeId, product_query: productQuery, draft: {}, uncertainty_codes: [] });
  }
  const products = [...rows.keys()];
  if (!products.length) return [];

  const assigned = new Map(products.map((productId) => [productId, []]));
  for (const [fieldId, role] of fields) {
    const ranked = products.map((productId) => {
      const key = associationKey(productId, fieldId);
      const score = Number(associationScores?.[key]);
      if (!Number.isFinite(score) || score < 0 || score > 1) throw new Error(`missing or invalid parser association score for ${productId}/${fieldId}`);
      return { score, productId };
    }).sort((left, right) => right.score - left.score || -lexicalCompare(left.productId, right.productId));
    const best = ranked[0];
    const secondScore = ranked.length > 1 ? ranked[1].score : 0;
    if (best.score < config.relation_threshold) continue;
    if (best.score - secondScore < config.relation_margin) {
      addUncertainty(rows.get(best.productId), "AMBIGUOUS_ASSOCIATION");
      continue;
    }
    assigned.get(best.productId).push({ score: best.score, role, fieldId });
  }

  const order = new Map(graph.nodeIds.map((nodeId, index) => [nodeId, index]));
  for (const productId of products) {
    const row = rows.get(productId);
    const candidates = assigned.get(productId);
    const selected = [];
    for (const role of ["dose", "frequency", "duration"]) {
      const choices = candidates.filter((item) => item.role === role).sort((left, right) =>
        right.score - left.score || -lexicalCompare(left.fieldId, right.fieldId));
      if (choices.length) selected.push(choices[0]);
    }
    selected.push(...candidates.filter((item) => item.role === "instruction" || item.role === "schedule"));
    selected.sort((left, right) => (order.get(left.fieldId) - order.get(right.fieldId)) || lexicalCompare(left.role, right.role));

    const draft = {};
    for (const item of selected) {
      const node = graph.nodes[graph.nodeIndex.get(item.fieldId)];
      const values = typedValues(item.role, node.text);
      if (!Object.keys(values).length) {
        addUncertainty(row, "UNPARSEABLE_FIELD");
        continue;
      }
      for (const [field, value] of Object.entries(values)) {
        if (Object.hasOwn(draft, field) && !sameValue(draft[field], value)) {
          delete draft[field];
          addUncertainty(row, "CONFLICTING_REGIMEN");
          continue;
        }
        draft[field] = value;
      }
    }

    if (draft.as_needed === true) {
      for (const field of ["frequency_per_day", "schedule_times"]) {
        if (Object.hasOwn(draft, field)) {
          delete draft[field];
          addUncertainty(row, "PRN_SUPPRESSED_FIXED_SCHEDULE");
        }
      }
    }
    if (Array.isArray(draft.schedule_times) && draft.schedule_times.length
        && Number.isInteger(draft.frequency_per_day)
        && draft.schedule_times.length !== draft.frequency_per_day) {
      delete draft.schedule_times;
      addUncertainty(row, "CONFLICTING_REGIMEN");
    }
    try {
      row.draft = normalizeDraft(draft);
    } catch (_error) {
      row.draft = {};
      addUncertainty(row, "CONFLICTING_REGIMEN");
    }
  }
  return products.map((productId) => rows.get(productId));
}

module.exports = {
  associationKey,
  decodeCandidates,
  decodeParserRows,
  relationScoresFromLogits,
};