function roleValue(text) {
  const value = String(text).replace(/\s+/g, "");
  const fraction = value.match(/(\d+)\/(\d+)/);
  if (fraction && Number(fraction[2]) !== 0) return Number(fraction[1]) / Number(fraction[2]);
  const match = value.match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
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
  const text = String(value || "");
  const found = [];
  for (const match of text.matchAll(/(오전|오후)\s*(\d{1,2})(?:\s*시)?(?:\s*[:：]\s*(\d{2}))?/giu)) {
    found.push(normalizedTime(match[1], match[2], match[3]));
  }
  for (const match of text.matchAll(/\b(AM|PM)\s*(\d{1,2})(?:\s*[:：]\s*(\d{2}))?\b/giu)) {
    found.push(normalizedTime(match[1], match[2], match[3]));
  }
  if (/(?:복용|투약|투여|복약)\s*시간|schedule\s*times?/iu.test(text)) {
    for (const match of text.matchAll(/(?:^|[^0-9])([01]?[0-9]|2[0-3])\s*[:：]\s*([0-5][0-9])(?![0-9])/gu)) {
      found.push(`${String(Number(match[1])).padStart(2, "0")}:${match[2]}`);
    }
  }
  return [...new Set(found.filter(Boolean))].sort();
}

function mealRelation(value) {
  const text = String(value || "");
  const candidates = [];
  if (/식사\s*(?:와|와 함께|중)|식중/iu.test(text)) candidates.push("with_meal");
  if (/식후|식사\s*후/iu.test(text)) candidates.push("after_meal");
  if (/식전|식사\s*전/iu.test(text)) candidates.push("before_meal");
  if (/공복|빈속/iu.test(text)) candidates.push("empty_stomach");
  if (/식사\s*(?:무관|관계\s*없이)/iu.test(text)) candidates.push("regardless");
  const unique = [...new Set(candidates)];
  return unique.length === 1 ? unique[0] : null;
}

function administrationRoute(value) {
  const text = String(value || "");
  if (/점안|안약|ophthalm/iu.test(text)) return "ophthalmic";
  if (/점이|귀에|otic/iu.test(text)) return "otic";
  if (/점비|비강|nasal/iu.test(text)) return "nasal";
  if (/흡입|inhal/iu.test(text)) return "inhaled";
  if (/주사|inject/iu.test(text)) return "injection";
  if (/외용|도포|바르|연고|크림|겔|로션|topical/iu.test(text)) return "topical";
  if (/경구|복용|먹|oral/iu.test(text)) return "oral";
  return null;
}

function regimenSemantics(region) {
  const text = String(region.text || "");
  const checkboxOptions = (text.match(/□/gu) || []).length;
  return {
    region_id: region.region_id,
    schedule_times: parsedTimes(text),
    meal_relation: checkboxOptions > 1 ? null : mealRelation(text),
    administration_route: administrationRoute(text),
    as_needed: checkboxOptions <= 1 && /필요시|필요\s*시|\bPRN\b|as\s+needed/iu.test(text),
  };
}

function buildExpectedRows(sample, includeRegimenSemantics) {
  const groups = new Map();
  for (const region of sample.regions) {
    const supportedRoles = includeRegimenSemantics
      ? ["product", "product_label", "dose", "frequency", "duration", "instruction", "schedule"]
      : ["product", "product_label", "dose", "frequency", "duration"];
    if (!supportedRoles.includes(region.semantic_role)) continue;
    if (region.association_group === "document") continue;
    const group = groups.get(region.association_group) ?? { product_labels: [], regimen_semantics: [] };
    if (region.semantic_role === "product_label") group.product_labels.push(region);
    else if (["instruction", "schedule"].includes(region.semantic_role)) group.regimen_semantics.push(regimenSemantics(region));
    else group[region.semantic_role] = region;
    groups.set(region.association_group, group);
  }
  const rows = [];
  for (const group of groups.values()) {
    if (!group.product) continue;
    const draft = {};
    const productEvidence = [...group.product_labels, group.product];
    const evidence = { product_query: productEvidence.map((region) => region.region_id) };
    if (group.dose) {
      const amount = roleValue(group.dose.text);
      if (amount !== null) {
        draft.dose_amount = amount;
        evidence.dose_amount = [group.dose.region_id];
        if (/포\s*\(정\)/.test(group.dose.text)) {
          draft.dosage_text = group.dose.text.replace(/\s+/g, "");
          evidence.dosage_text = [group.dose.region_id];
        } else {
          const compact = group.dose.text.replace(/\s+/g, "").toLowerCase();
          let unit = null;
          if (compact.includes("캡슐") || compact.includes("capsule")) unit = "capsule";
          else if (compact.includes("정") || compact.includes("tablet")) unit = "tablet";
          else if (compact.includes("포")) unit = "packet";
          else if (compact.includes("ml")) unit = "mL";
          if (unit) {
            draft.dose_unit = unit;
            evidence.dose_unit = [group.dose.region_id];
          }
        }
      }
    }
    if (group.frequency) {
      const value = roleValue(group.frequency.text);
      if (value !== null) {
        draft.frequency_per_day = value;
        evidence.frequency_per_day = [group.frequency.region_id];
      }
    }
    if (group.duration) {
      const value = roleValue(group.duration.text);
      if (value !== null) {
        draft.prescription_days = value;
        evidence.prescription_days = [group.duration.region_id];
      }
    }
    if (includeRegimenSemantics) {
      const scheduleTimes = [...new Set(group.regimen_semantics.flatMap((item) => item.schedule_times))].sort();
      if (scheduleTimes.length) {
        draft.schedule_times = scheduleTimes;
        evidence.schedule_times = group.regimen_semantics.filter((item) => item.schedule_times.length).map((item) => item.region_id);
      }
      for (const field of ["meal_relation", "administration_route"]) {
        const candidates = [...new Set(group.regimen_semantics.map((item) => item[field]).filter(Boolean))];
        if (candidates.length === 1) {
          draft[field] = candidates[0];
          evidence[field] = group.regimen_semantics.filter((item) => item[field] === candidates[0]).map((item) => item.region_id);
        }
      }
      const asNeededEvidence = group.regimen_semantics.filter((item) => item.as_needed).map((item) => item.region_id);
      if (asNeededEvidence.length) {
        draft.as_needed = true;
        evidence.as_needed = asNeededEvidence;
      }
    }
    rows.push({
      row_id: productEvidence[0].region_id,
      product_query: group.product.text.trim(),
      draft,
      uncertainty_codes: [],
      evidence,
    });
  }
  return rows;
}

export function expectedRows(sample) {
  return buildExpectedRows(sample, false);
}

export function parserTrainingRows(sample) {
  return buildExpectedRows(sample, true);
}

export function positiveEdges(sample) {
  const products = sample.regions.filter((region) => region.semantic_role === "product" && region.association_group !== "document");
  const fields = sample.regions.filter((region) => ["dose", "frequency", "duration", "instruction", "schedule"].includes(region.semantic_role) && region.association_group !== "document");
  const edges = [];
  for (const product of products) {
    for (const field of fields) {
      if (product.association_group === field.association_group) {
        edges.push({ product_node_id: product.region_id, field_node_id: field.region_id, relation: "same_medication" });
      }
    }
  }
  return edges;
}

export function buildParsingItems(corpus) {
  return corpus.samples.map((sample) => ({
    document_id: sample.id,
    split: sample.split,
    drug_name_split: sample.drug_name_split,
    drug_name_exposure: sample.drug_name_exposure,
    image_sha256: sample.image_sha256,
    width: sample.width,
    height: sample.height,
    layout_family: sample.layout_family,
    parser_structure_variant: sample.parser_structure_variant,
    capture_profile: sample.capture_profile,
    augmentation_difficulty: sample.augmentation_difficulty,
    augmentation_components: sample.capture.augmentation_components,
    scenario_tags: sample.scenario_tags,
    risk_tags: sample.risk_tags,
    nodes: sample.regions.map((region) => ({
      node_id: region.region_id,
      text: region.text,
      confidence: 1.0,
      polygon: region.polygon,
      natural_text_polygon: region.natural_text_polygon,
      semantic_role: region.semantic_role,
      association_group: region.association_group,
      ...(region.drug_family ? {
        drug_family: region.drug_family,
        drug_name_split: region.drug_name_split,
      } : {}),
      region_class: region.region_class,
      critical: region.critical,
    })),
    positive_edges: positiveEdges(sample),
    expected_rows: parserTrainingRows(sample),
  }));
}

export function buildOracleManifest(corpus) {
  return {
    schema_version: 2,
    cases: corpus.samples.map((sample) => ({
      case_id: sample.id,
      source_kind: "synthetic",
      scenario_tags: sample.scenario_tags,
      risk_tags: sample.risk_tags,
      boxes: sample.regions.map((region) => ({
        box_id: region.region_id,
        text: region.text,
        confidence: 1.0,
        polygon: region.polygon,
      })),
      expected_rows: expectedRows(sample),
    })),
  };
}
