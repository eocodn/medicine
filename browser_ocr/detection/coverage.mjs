import {
  AUGMENTATION_DIFFICULTIES,
  BACKGROUND_PROFILES,
  CAPTURE_PROFILES,
  LEGACY_LAYOUT_FAMILIES,
  LAYOUT_FAMILIES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
  REQUIRED_AUGMENTATION_COMPONENTS,
  REQUIRED_CRITICAL_SEMANTIC_ROLES,
  REQUIRED_RISK_TAGS,
  REQUIRED_V6_AUGMENTATION_COMPONENTS,
  REQUIRED_V6_RISK_TAGS,
  SCENE_PROP_PROFILES,
} from "./synthetic_catalog.mjs";

function counts(values) {
  const result = Object.create(null);
  for (const value of values) result[value] = (result[value] || 0) + 1;
  return result;
}

function sortedEntries(counter) {
  return Object.entries(counter).sort(([left], [right]) => left.localeCompare(right));
}

export function auditCoverage(corpus, {
  minimumPerLayout = 1,
  minimumPerCapture = 1,
  minimumPerRisk = 1,
  minimumCriticalPerRole = 1,
} = {}) {
  const requiredLayoutFamilies = corpus.generator?.version >= 6 ? LAYOUT_FAMILIES : LEGACY_LAYOUT_FAMILIES;
  const layoutCounts = counts(corpus.samples.map((sample) => sample.layout_family));
  const captureCounts = counts(corpus.samples.map((sample) => sample.capture_profile));
  const difficultyCounts = counts(corpus.samples.map((sample) => sample.augmentation_difficulty));
  const augmentationComponentCounts = counts(corpus.samples.flatMap((sample) => sample.capture.augmentation_components || []));
  const materialCounts = counts(corpus.samples.map((sample) => sample.material_profile));
  const printerCounts = counts(corpus.samples.map((sample) => sample.printer_profile));
  const backgroundCounts = counts(corpus.samples.map((sample) => sample.background_profile));
  const scenePropCounts = counts(corpus.samples.map((sample) => sample.scene_prop_profile));
  const visualStyleCounts = counts(corpus.samples.map((sample) => sample.visual_style).filter(Boolean));
  const riskCounts = counts(corpus.samples.flatMap((sample) => sample.risk_tags));
  const semanticCounts = counts(corpus.samples.flatMap((sample) => sample.regions.map((region) => region.semantic_role)));
  const classCounts = counts(corpus.samples.flatMap((sample) => sample.regions.map((region) => region.region_class)));
  const criticalSemanticCounts = counts(corpus.samples.flatMap((sample) => (
    sample.regions.filter((region) => region.critical).map((region) => region.semantic_role)
  )));
  const failures = [];

  for (const family of requiredLayoutFamilies) {
    if ((layoutCounts[family] || 0) < minimumPerLayout) failures.push(`layout family ${family} < ${minimumPerLayout}`);
  }
  for (const profile of CAPTURE_PROFILES) {
    if ((captureCounts[profile] || 0) < minimumPerCapture) failures.push(`capture profile ${profile} < ${minimumPerCapture}`);
  }
  for (const difficulty of AUGMENTATION_DIFFICULTIES) {
    if ((difficultyCounts[difficulty] || 0) < 1) failures.push(`augmentation difficulty ${difficulty} < 1`);
  }
  for (const component of REQUIRED_AUGMENTATION_COMPONENTS) {
    if ((augmentationComponentCounts[component] || 0) < 1) failures.push(`augmentation component ${component} < 1`);
  }
  if (corpus.generator?.version >= 6) {
    for (const component of REQUIRED_V6_AUGMENTATION_COMPONENTS) {
      if ((augmentationComponentCounts[component] || 0) < 1) failures.push(`augmentation component ${component} < 1`);
    }
  }
  for (const profile of MATERIAL_PROFILES) {
    if ((materialCounts[profile] || 0) < 1) failures.push(`material profile ${profile} < 1`);
  }
  for (const profile of PRINTER_PROFILES) {
    if ((printerCounts[profile] || 0) < 1) failures.push(`printer profile ${profile} < 1`);
  }
  for (const profile of BACKGROUND_PROFILES) {
    if ((backgroundCounts[profile] || 0) < 1) failures.push(`background profile ${profile} < 1`);
  }
  if (corpus.generator?.version >= 6 && corpus.generator?.revision >= 6) {
    for (const profile of SCENE_PROP_PROFILES) {
      if ((scenePropCounts[profile] || 0) < 1) failures.push(`scene prop profile ${profile} < 1`);
    }
  }
  for (const risk of REQUIRED_RISK_TAGS) {
    if ((riskCounts[risk] || 0) < minimumPerRisk) failures.push(`risk tag ${risk} < ${minimumPerRisk}`);
  }
  if (corpus.generator?.version >= 6) {
    for (const risk of REQUIRED_V6_RISK_TAGS) {
      if ((riskCounts[risk] || 0) < minimumPerRisk) failures.push(`risk tag ${risk} < ${minimumPerRisk}`);
    }
  }
  for (const role of REQUIRED_CRITICAL_SEMANTIC_ROLES) {
    if ((criticalSemanticCounts[role] || 0) < minimumCriticalPerRole) {
      failures.push(`critical semantic role ${role} < ${minimumCriticalPerRole}`);
    }
  }
  if ((classCounts.distractor || 0) < 1) failures.push("region class distractor is absent");
  if ((classCounts.medication || 0) < 1) failures.push("region class medication is absent");

  return {
    schema_version: 1,
    corpus_id: corpus.corpus_id,
    status: failures.length ? "fail" : "pass",
    samples: corpus.samples.length,
    regions: corpus.samples.reduce((sum, sample) => sum + sample.regions.length, 0),
    critical_regions: corpus.samples.reduce((sum, sample) => sum + sample.regions.filter((region) => region.critical).length, 0),
    layout_families: sortedEntries(layoutCounts),
    capture_profiles: sortedEntries(captureCounts),
    augmentation_difficulties: sortedEntries(difficultyCounts),
    augmentation_components: augmentationComponentCounts,
    material_profiles: sortedEntries(materialCounts),
    printer_profiles: sortedEntries(printerCounts),
    background_profiles: sortedEntries(backgroundCounts),
    scene_prop_profiles: sortedEntries(scenePropCounts),
    visual_styles: sortedEntries(visualStyleCounts),
    risk_tags: riskCounts,
    semantic_roles: semanticCounts,
    critical_semantic_roles: criticalSemanticCounts,
    region_classes: classCounts,
    failures,
  };
}