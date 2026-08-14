function normalized(value) {
  return String(value || "").normalize("NFC").replace(/\s+/gu, "").trim();
}

function editDistance(left, right) {
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let i = 1; i <= left.length; i += 1) {
    let diagonal = previous[0];
    previous[0] = i;
    for (let j = 1; j <= right.length; j += 1) {
      const above = previous[j];
      previous[j] = Math.min(
        previous[j] + 1,
        previous[j - 1] + 1,
        diagonal + (left[i - 1] === right[j - 1] ? 0 : 1),
      );
      diagonal = above;
    }
  }
  return previous[right.length];
}

function numberInRange(value, minimum, maximum, label) {
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error(`${label} must be between ${minimum} and ${maximum}`);
  }
}

function assertStringArray(value, label, { allowEmpty = true } = {}) {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)
      || value.some((item) => typeof item !== "string" || !item.trim())) {
    throw new Error(`${label} must be an array of non-empty strings`);
  }
}

function validateTransform(transform, sampleId) {
  if (transform === undefined) return;
  if (!transform || typeof transform !== "object" || Array.isArray(transform)) {
    throw new Error(`${sampleId}.transform must be an object`);
  }
  const bounds = {
    rotation_degrees: [-12, 12],
    scale: [0.35, 1],
    blur_px: [0, 2],
    brightness: [0.5, 1.5],
    contrast: [0.5, 1.5],
    noise: [0, 0.12],
  };
  for (const key of Object.keys(transform)) {
    if (!(key in bounds)) throw new Error(`${sampleId}.transform.${key} is unsupported`);
    numberInRange(Number(transform[key]), bounds[key][0], bounds[key][1], `${sampleId}.transform.${key}`);
  }
}

export function validateCorpus(corpus) {
  if (!corpus || typeof corpus !== "object" || corpus.schema_version !== 2) {
    throw new Error("unsupported OCR evaluation corpus schema_version; expected 2");
  }
  if (typeof corpus.corpus_id !== "string" || !corpus.corpus_id.trim()) {
    throw new Error("corpus_id is required");
  }
  if (!Array.isArray(corpus.samples) || corpus.samples.length === 0) {
    throw new Error("OCR evaluation corpus must contain samples");
  }
  const gates = corpus.gates || {};
  for (const key of [
    "max_character_error_rate",
    "critical_token_recall",
    "numeric_token_recall",
    "layout_line_recall",
    "layout_order_accuracy",
    "no_text_sample_pass_rate",
  ]) {
    numberInRange(Number(gates[key]), 0, 1, `gates.${key}`);
  }

  const ids = new Set();
  for (const sample of corpus.samples) {
    if (!sample || typeof sample !== "object") throw new Error("sample must be an object");
    if (typeof sample.id !== "string" || !/^[a-z0-9_-]+$/.test(sample.id)) {
      throw new Error("sample.id must use lowercase ASCII letters, digits, hyphen, or underscore");
    }
    if (ids.has(sample.id)) throw new Error(`duplicate sample id: ${sample.id}`);
    ids.add(sample.id);
    if (typeof sample.image !== "string" || !sample.image.trim()) throw new Error(`${sample.id}.image is required`);
    if (typeof sample.expected_text !== "string") throw new Error(`${sample.id}.expected_text must be a string`);
    assertStringArray(sample.critical_tokens, `${sample.id}.critical_tokens`);
    assertStringArray(sample.numeric_tokens, `${sample.id}.numeric_tokens`);
    if (sample.expected_lines !== undefined) {
      if (!Array.isArray(sample.expected_lines) || sample.expected_lines.length === 0) {
        throw new Error(`${sample.id}.expected_lines must be a non-empty array`);
      }
      sample.expected_lines.forEach((line, index) => assertStringArray(
        line,
        `${sample.id}.expected_lines[${index}]`,
        { allowEmpty: false },
      ));
    }
    if (sample.scenario_tags !== undefined) {
      assertStringArray(sample.scenario_tags, `${sample.id}.scenario_tags`, { allowEmpty: false });
      if (new Set(sample.scenario_tags).size !== sample.scenario_tags.length) {
        throw new Error(`${sample.id}.scenario_tags must be unique`);
      }
    }
    if (sample.expect_no_text !== undefined && typeof sample.expect_no_text !== "boolean") {
      throw new Error(`${sample.id}.expect_no_text must be boolean`);
    }
    validateTransform(sample.transform, sample.id);
  }
  return corpus;
}

function polygonCenterY(poly) {
  if (!Array.isArray(poly) || poly.length < 3) return null;
  const ys = poly.map((point) => Array.isArray(point) ? Number(point[1]) : NaN);
  if (ys.some((value) => !Number.isFinite(value))) return null;
  return ys.reduce((sum, value) => sum + value, 0) / ys.length;
}

function evaluateExpectedLines(expectedLines, items) {
  if (!expectedLines?.length) {
    return { found: 0, total: 0, orderCorrect: 0, orderTotal: 0, issueCodes: [] };
  }
  const unused = new Set(items.map((_, index) => index));
  const matched = [];
  for (const tokens of expectedLines) {
    const index = [...unused].find((candidate) => {
      const text = normalized(items[candidate]?.text);
      return tokens.every((token) => text.includes(normalized(token)));
    });
    if (index === undefined) {
      matched.push(null);
      continue;
    }
    unused.delete(index);
    matched.push({ index, centerY: polygonCenterY(items[index]?.poly) });
  }

  let orderCorrect = 0;
  let orderTotal = 0;
  for (let index = 1; index < matched.length; index += 1) {
    const previous = matched[index - 1];
    const current = matched[index];
    if (!previous || !current || previous.centerY === null || current.centerY === null) continue;
    orderTotal += 1;
    if (previous.centerY < current.centerY) orderCorrect += 1;
  }
  const found = matched.filter(Boolean).length;
  const issueCodes = [];
  if (found < expectedLines.length) issueCodes.push("LAYOUT_LINE");
  if (orderTotal > 0 && orderCorrect < orderTotal) issueCodes.push("LAYOUT_ORDER");
  return { found, total: expectedLines.length, orderCorrect, orderTotal, issueCodes };
}

function summarize(results) {
  let totalExpectedCharacters = 0;
  let totalCharacterErrors = 0;
  let criticalTotal = 0;
  let criticalFound = 0;
  let numericTotal = 0;
  let numericFound = 0;
  let layoutLinesTotal = 0;
  let layoutLinesFound = 0;
  let layoutOrderTotal = 0;
  let layoutOrderCorrect = 0;
  let noTextTotal = 0;
  let noTextPassed = 0;
  let maxSampleCer = 0;
  let wallMs = 0;

  for (const sample of results) {
    totalExpectedCharacters += sample._expected_characters;
    totalCharacterErrors += sample._character_errors;
    criticalTotal += sample.critical_tokens_total;
    criticalFound += sample.critical_tokens_found.length;
    numericTotal += sample.numeric_tokens_total;
    numericFound += sample.numeric_tokens_found.length;
    layoutLinesTotal += sample.layout_lines_total;
    layoutLinesFound += sample.layout_lines_found;
    layoutOrderTotal += sample.layout_order_pairs_total;
    layoutOrderCorrect += sample.layout_order_pairs_correct;
    if (sample.expect_no_text) {
      noTextTotal += 1;
      if (sample.no_text_passed) noTextPassed += 1;
    }
    maxSampleCer = Math.max(maxSampleCer, sample.character_error_rate);
    wallMs += sample.wall_ms;
  }

  return {
    sample_count: results.length,
    character_error_rate: totalExpectedCharacters ? totalCharacterErrors / totalExpectedCharacters : 0,
    max_sample_character_error_rate: maxSampleCer,
    critical_token_recall: criticalTotal ? criticalFound / criticalTotal : 1,
    numeric_token_recall: numericTotal ? numericFound / numericTotal : 1,
    layout_line_recall: layoutLinesTotal ? layoutLinesFound / layoutLinesTotal : 1,
    layout_order_accuracy: layoutOrderTotal ? layoutOrderCorrect / layoutOrderTotal : 1,
    no_text_sample_pass_rate: noTextTotal ? noTextPassed / noTextTotal : 1,
    wall_ms: wallMs,
  };
}

function publicSample(sample) {
  const {
    _expected_characters: _expectedCharacters,
    _character_errors: _characterErrors,
    ...visible
  } = sample;
  return visible;
}

function matchedTokenOccurrences(expectedTokens, recognizedCompact) {
  const consumed = new Map();
  const found = [];
  for (const token of expectedTokens) {
    const compact = normalized(token);
    if (!compact) continue;
    const alreadyConsumed = consumed.get(compact) || 0;
    let occurrences = 0;
    let offset = 0;
    while (offset <= recognizedCompact.length - compact.length) {
      const index = recognizedCompact.indexOf(compact, offset);
      if (index < 0) break;
      occurrences += 1;
      offset = index + compact.length;
    }
    if (alreadyConsumed < occurrences) {
      found.push(token);
      consumed.set(compact, alreadyConsumed + 1);
    }
  }
  return found;
}

export function evaluateCorpus(corpus, rawResult) {
  validateCorpus(corpus);
  if (!rawResult || !Array.isArray(rawResult.samples)) throw new Error("OCR evaluation result is missing samples");
  const byId = new Map(rawResult.samples.map((sample) => [sample.id, sample]));
  const sampleResults = [];

  for (const expected of corpus.samples) {
    const observed = byId.get(expected.id);
    if (!observed || !Array.isArray(observed.items)) throw new Error(`missing OCR result for ${expected.id}`);
    const recognizedItems = observed.items.filter((item) => normalized(item?.text));
    const recognizedText = recognizedItems.map((item) => item.text).join(" ");
    const expectedCompact = normalized(expected.expected_text);
    const recognizedCompact = normalized(recognizedText);
    const errors = editDistance(expectedCompact, recognizedCompact);
    const cer = expectedCompact.length ? errors / expectedCompact.length : 0;
    const critical = expected.critical_tokens || [];
    const foundCritical = matchedTokenOccurrences(critical, recognizedCompact);
    const numeric = expected.numeric_tokens || [];
    const foundNumeric = matchedTokenOccurrences(numeric, recognizedCompact);
    const layout = evaluateExpectedLines(expected.expected_lines, recognizedItems);
    const scores = recognizedItems.map((item) => Number(item.score)).filter(Number.isFinite);
    const noTextPassed = expected.expect_no_text ? recognizedItems.length === 0 : null;
    const issues = [];
    if (expected.expect_no_text && !noTextPassed) issues.push("UNEXPECTED_TEXT");
    if (cer > Number(corpus.gates.max_character_error_rate)) issues.push("TEXT_ERROR");
    if (foundCritical.length < critical.length) issues.push("CRITICAL_TOKEN");
    if (foundNumeric.length < numeric.length) issues.push("NUMERIC_TOKEN");
    issues.push(...layout.issueCodes);

    sampleResults.push({
      id: expected.id,
      wall_ms: Number(observed.wall_ms) || 0,
      expected_text: expected.expected_text,
      recognized_text: recognizedText,
      character_error_rate: cer,
      critical_tokens_found: foundCritical,
      critical_tokens_total: critical.length,
      numeric_tokens_found: foundNumeric,
      numeric_tokens_total: numeric.length,
      layout_lines_found: layout.found,
      layout_lines_total: layout.total,
      layout_order_pairs_correct: layout.orderCorrect,
      layout_order_pairs_total: layout.orderTotal,
      expect_no_text: Boolean(expected.expect_no_text),
      no_text_passed: noTextPassed,
      mean_token_score: scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null,
      scenario_tags: expected.scenario_tags || [],
      transform: expected.transform || null,
      issues,
      items: observed.items,
      _expected_characters: expectedCompact.length,
      _character_errors: errors,
    });
  }

  const metrics = summarize(sampleResults);
  const gates = corpus.gates;
  const passed = metrics.max_sample_character_error_rate <= Number(gates.max_character_error_rate)
    && metrics.critical_token_recall >= Number(gates.critical_token_recall)
    && metrics.numeric_token_recall >= Number(gates.numeric_token_recall)
    && metrics.layout_line_recall >= Number(gates.layout_line_recall)
    && metrics.layout_order_accuracy >= Number(gates.layout_order_accuracy)
    && metrics.no_text_sample_pass_rate >= Number(gates.no_text_sample_pass_rate);

  const scenarioNames = new Set(sampleResults.flatMap((sample) => sample.scenario_tags));
  const scenarios = {};
  for (const name of [...scenarioNames].sort()) {
    scenarios[name] = summarize(sampleResults.filter((sample) => sample.scenario_tags.includes(name)));
  }

  return {
    status: passed ? "pass" : "fail",
    gates,
    metrics,
    scenarios,
    samples: sampleResults.map(publicSample),
  };
}
