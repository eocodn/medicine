import { finiteNumber, validatePolygon, validateUnifiedCorpus } from "../corpus/contract.mjs";

export const validateCorpus = validateUnifiedCorpus;

export function validatePredictions(input, corpus) {
  if (!input || typeof input !== "object" || input.schema_version !== 1) throw new Error("invalid detection predictions: schema_version must be 1");
  if (input.corpus_id !== corpus.corpus_id) throw new Error("invalid detection predictions: corpus_id mismatch");
  if (!Array.isArray(input.samples)) throw new Error("invalid detection predictions: samples must be an array");
  const expectedIds = new Set(corpus.samples.map((sample) => sample.id));
  const seen = new Set();
  for (const sample of input.samples) {
    if (!expectedIds.has(sample.id)) throw new Error(`invalid detection predictions: unknown sample ${sample.id}`);
    if (seen.has(sample.id)) throw new Error(`invalid detection predictions: duplicate sample ${sample.id}`);
    seen.add(sample.id);
    if (!Array.isArray(sample.predictions)) throw new Error(`invalid detection predictions: ${sample.id}.predictions must be an array`);
    const gt = corpus.samples.find((candidate) => candidate.id === sample.id);
    for (const [index, prediction] of sample.predictions.entries()) {
      validatePolygon(prediction.polygon, gt.width, gt.height, `prediction ${sample.id}[${index}].polygon`);
      finiteNumber(prediction.score, `prediction ${sample.id}[${index}].score`);
      if (prediction.score < 0 || prediction.score > 1) throw new Error(`invalid detection predictions: ${sample.id}[${index}].score must be between 0 and 1`);
    }
  }
  if (seen.size !== expectedIds.size) throw new Error("invalid detection predictions: every corpus sample must be present exactly once");
  return structuredClone(input);
}
