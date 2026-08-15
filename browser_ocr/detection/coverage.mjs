import {
  BACKGROUND_PROFILES,
  CAPTURE_PROFILES,
  LAYOUT_FAMILIES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
  REQUIRED_CRITICAL_SEMANTIC_ROLES,
  REQUIRED_RISK_TAGS,
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
  const layoutCounts = counts(corpus.samples.map((sample) => sample.layout_family));
  const captureCounts = counts(corpus.samples.map((sample) => sample.capture_profile));
  const materialCounts = counts(corpus.samples.map((sample) => sample.material_profile));
  const printerCounts = counts(corpus.samples.map((sample) => sample.printer_profile));
  const backgroundCounts = counts(corpus.samples.map((sample) => sample.background_profile));
  const riskCounts = counts(corpus.samples.flatMap((sample) => sample.risk_tags));
  const semanticCounts = counts(corpus.samples.flatMap((sample) => sample.regions.map((region) => region.semantic_role)));
  const classCounts = counts(corpus.samples.flatMap((sample) => sample.regions.map((region) => region.region_class)));
  const criticalSemanticCounts = counts(corpus.samples.flatMap((sample) => (
    sample.regions.filter((region) => region.critical).map((region) => region.semantic_role)
  )));
  const failures = [];

  for (const family of LAYOUT_FAMILIES) {
    if ((layoutCounts[family] || 0) < minimumPerLayout) failures.push(`layout family ${family} < ${minimumPerLayout}`);
  }
  for (const profile of CAPTURE_PROFILES) {
    if ((captureCounts[profile] || 0) < minimumPerCapture) failures.push(`capture profile ${profile} < ${minimumPerCapture}`);
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
  for (const risk of REQUIRED_RISK_TAGS) {
    if ((riskCounts[risk] || 0) < minimumPerRisk) failures.push(`risk tag ${risk} < ${minimumPerRisk}`);
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
    material_profiles: sortedEntries(materialCounts),
    printer_profiles: sortedEntries(printerCounts),
    background_profiles: sortedEntries(backgroundCounts),
    risk_tags: riskCounts,
    semantic_roles: semanticCounts,
    critical_semantic_roles: criticalSemanticCounts,
    region_classes: classCounts,
    failures,
  };
}