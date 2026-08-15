import { validatePredictions } from "./contract.mjs";

function polygonArea(polygon) {
  let sum = 0;
  for (let index = 0; index < polygon.length; index += 1) {
    const [x1, y1] = polygon[index];
    const [x2, y2] = polygon[(index + 1) % polygon.length];
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
}

function signedArea(polygon) {
  let sum = 0;
  for (let index = 0; index < polygon.length; index += 1) {
    const [x1, y1] = polygon[index];
    const [x2, y2] = polygon[(index + 1) % polygon.length];
    sum += x1 * y2 - x2 * y1;
  }
  return sum / 2;
}

function lineIntersection(a, b, c, d) {
  const denominator = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0]);
  if (Math.abs(denominator) < 1e-9) return b;
  const determinantA = a[0] * b[1] - a[1] * b[0];
  const determinantB = c[0] * d[1] - c[1] * d[0];
  return [
    (determinantA * (c[0] - d[0]) - (a[0] - b[0]) * determinantB) / denominator,
    (determinantA * (c[1] - d[1]) - (a[1] - b[1]) * determinantB) / denominator,
  ];
}

function clipConvex(subject, clip) {
  let output = subject.map((point) => [...point]);
  const clockwise = signedArea(clip) < 0;
  const inside = (point, edgeStart, edgeEnd) => {
    const cross = (edgeEnd[0] - edgeStart[0]) * (point[1] - edgeStart[1])
      - (edgeEnd[1] - edgeStart[1]) * (point[0] - edgeStart[0]);
    return clockwise ? cross <= 1e-9 : cross >= -1e-9;
  };
  for (let edgeIndex = 0; edgeIndex < clip.length; edgeIndex += 1) {
    const edgeStart = clip[edgeIndex];
    const edgeEnd = clip[(edgeIndex + 1) % clip.length];
    const input = output;
    output = [];
    if (input.length === 0) break;
    let previous = input.at(-1);
    for (const current of input) {
      const currentInside = inside(current, edgeStart, edgeEnd);
      const previousInside = inside(previous, edgeStart, edgeEnd);
      if (currentInside) {
        if (!previousInside) output.push(lineIntersection(previous, current, edgeStart, edgeEnd));
        output.push(current);
      } else if (previousInside) {
        output.push(lineIntersection(previous, current, edgeStart, edgeEnd));
      }
      previous = current;
    }
  }
  return output;
}

function overlap(a, b) {
  const areaA = polygonArea(a);
  const areaB = polygonArea(b);
  const intersectionPolygon = clipConvex(a, b);
  const intersection = intersectionPolygon.length >= 3 ? polygonArea(intersectionPolygon) : 0;
  const union = areaA + areaB - intersection;
  return {
    intersection,
    iou: union > 0 ? intersection / union : 0,
    fractionA: areaA > 0 ? intersection / areaA : 0,
    fractionB: areaB > 0 ? intersection / areaB : 0,
  };
}

function greedyMatches(regions, predictions, threshold = 0.5) {
  const candidates = [];
  for (let gtIndex = 0; gtIndex < regions.length; gtIndex += 1) {
    for (let predIndex = 0; predIndex < predictions.length; predIndex += 1) {
      const { iou } = overlap(regions[gtIndex].polygon, predictions[predIndex].polygon);
      if (iou >= threshold) candidates.push({ gtIndex, predIndex, iou });
    }
  }
  candidates.sort((a, b) => b.iou - a.iou);
  const gtUsed = new Set();
  const predUsed = new Set();
  const matches = [];
  for (const candidate of candidates) {
    if (gtUsed.has(candidate.gtIndex) || predUsed.has(candidate.predIndex)) continue;
    gtUsed.add(candidate.gtIndex);
    predUsed.add(candidate.predIndex);
    matches.push(candidate);
  }
  return matches;
}

function evaluateSample(sample, predictionSample) {
  const predictions = predictionSample.predictions;
  const matches = greedyMatches(sample.regions, predictions);
  const matchedGt = new Set(matches.map((match) => match.gtIndex));
  const matchedPred = new Set(matches.map((match) => match.predIndex));

  let mergeErrors = 0;
  let crossAssociationMerges = 0;
  for (const prediction of predictions) {
    const covered = sample.regions.filter((region) => overlap(region.polygon, prediction.polygon).fractionA >= 0.6);
    if (covered.length >= 2) {
      mergeErrors += 1;
      if (new Set(covered.map((region) => region.association_group)).size >= 2) crossAssociationMerges += 1;
    }
  }

  let splitErrors = 0;
  for (const region of sample.regions) {
    const parts = predictions.filter((prediction) => {
      const item = overlap(region.polygon, prediction.polygon);
      return item.fractionA >= 0.2 && item.fractionB >= 0.5;
    });
    if (parts.length >= 2) splitErrors += 1;
  }

  const criticalIndices = sample.regions
    .map((region, index) => ({ region, index }))
    .filter(({ region }) => region.critical)
    .map(({ index }) => index);
  const criticalMatched = criticalIndices.filter((index) => matchedGt.has(index)).length;

  return {
    id: sample.id,
    scenario_tags: sample.scenario_tags,
    risk_tags: sample.risk_tags,
    ground_truth_boxes: sample.regions.length,
    predicted_boxes: predictions.length,
    matched_boxes: matches.length,
    unmatched_ground_truth: sample.regions.length - matchedGt.size,
    unmatched_predictions: predictions.length - matchedPred.size,
    critical_boxes: criticalIndices.length,
    critical_matched: criticalMatched,
    merge_errors: mergeErrors,
    cross_association_merges: crossAssociationMerges,
    split_errors: splitErrors,
  };
}

function ratio(numerator, denominator) {
  return denominator ? numerator / denominator : 1;
}

export function evaluateDetections(corpus, predictionInput) {
  const predictions = validatePredictions(predictionInput, corpus);
  const byId = new Map(predictions.samples.map((sample) => [sample.id, sample]));
  const samples = corpus.samples.map((sample) => evaluateSample(sample, byId.get(sample.id)));
  const totals = samples.reduce((sum, sample) => ({
    ground_truth_boxes: sum.ground_truth_boxes + sample.ground_truth_boxes,
    predicted_boxes: sum.predicted_boxes + sample.predicted_boxes,
    matched_boxes: sum.matched_boxes + sample.matched_boxes,
    critical_boxes: sum.critical_boxes + sample.critical_boxes,
    critical_matched: sum.critical_matched + sample.critical_matched,
    merge_errors: sum.merge_errors + sample.merge_errors,
    cross_association_merges: sum.cross_association_merges + sample.cross_association_merges,
    split_errors: sum.split_errors + sample.split_errors,
  }), {
    ground_truth_boxes: 0,
    predicted_boxes: 0,
    matched_boxes: 0,
    critical_boxes: 0,
    critical_matched: 0,
    merge_errors: 0,
    cross_association_merges: 0,
    split_errors: 0,
  });
  const recall = ratio(totals.matched_boxes, totals.ground_truth_boxes);
  const precision = ratio(totals.matched_boxes, totals.predicted_boxes);
  const criticalBoxRecall = ratio(totals.critical_matched, totals.critical_boxes);
  const metrics = {
    recall,
    precision,
    hmean: recall + precision ? (2 * recall * precision) / (recall + precision) : 0,
    critical_box_recall: criticalBoxRecall,
    merge_errors: totals.merge_errors,
    cross_association_merges: totals.cross_association_merges,
    split_errors: totals.split_errors,
    ground_truth_boxes: totals.ground_truth_boxes,
    predicted_boxes: totals.predicted_boxes,
    matched_boxes: totals.matched_boxes,
  };
  const gates = corpus.gates;
  const pass = recall >= gates.min_recall
    && precision >= gates.min_precision
    && criticalBoxRecall >= gates.min_critical_box_recall
    && totals.merge_errors <= gates.max_merge_errors
    && totals.cross_association_merges <= gates.max_cross_association_merges
    && totals.split_errors <= gates.max_split_errors;
  return { schema_version: 1, corpus_id: corpus.corpus_id, status: pass ? "pass" : "fail", gates, metrics, samples };
}

export const geometry = { polygonArea, overlap };