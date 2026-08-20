function roleValue(text) {
  const value = String(text).replace(/\s+/g, "");
  const fraction = value.match(/(\d+)\/(\d+)/);
  if (fraction && Number(fraction[2]) !== 0) return Number(fraction[1]) / Number(fraction[2]);
  const match = value.match(/\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

export function expectedRows(sample) {
  const groups = new Map();
  for (const region of sample.regions) {
    if (!["product", "product_label", "dose", "frequency", "duration"].includes(region.semantic_role)) continue;
    if (region.association_group === "document") continue;
    const group = groups.get(region.association_group) ?? { product_labels: [] };
    if (region.semantic_role === "product_label") group.product_labels.push(region);
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

export function positiveEdges(sample) {
  const products = sample.regions.filter((region) => region.semantic_role === "product" && region.association_group !== "document");
  const fields = sample.regions.filter((region) => ["dose", "frequency", "duration", "instruction"].includes(region.semantic_role) && region.association_group !== "document");
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
    expected_rows: expectedRows(sample),
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
