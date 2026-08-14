"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const OCR = path.resolve(__dirname, "..");

async function metricsModule() {
  return import(`${pathToFileURL(path.join(OCR, "eval/metrics.mjs")).href}?t=${Date.now()}`);
}

function baseCorpus(samples) {
  return {
    schema_version: 2,
    corpus_id: "unit-eval-v2",
    gates: {
      max_character_error_rate: 0.2,
      critical_token_recall: 1,
      numeric_token_recall: 1,
      layout_line_recall: 1,
      layout_order_accuracy: 1,
      no_text_sample_pass_rate: 1,
    },
    samples,
  };
}

function box(text, top, bottom = top + 20, score = 0.95) {
  return { text, score, poly: [[10, top], [200, top], [200, bottom], [10, bottom]] };
}

test("evaluation metrics gate model text, row geometry, negative controls, and scenario summaries", async () => {
  const { evaluateCorpus, validateCorpus } = await metricsModule();
  const corpus = baseCorpus([
    {
      id: "table",
      image: "images/table.svg",
      expected_text: "타이레놀정 1정 이부프로펜정 2정",
      critical_tokens: ["타이레놀정", "이부프로펜정"],
      numeric_tokens: ["1정", "2정"],
      expected_lines: [["타이레놀정", "1정"], ["이부프로펜정", "2정"]],
      scenario_tags: ["layout", "table"],
    },
    {
      id: "blank",
      image: "images/blank.svg",
      expected_text: "",
      critical_tokens: [],
      numeric_tokens: [],
      expect_no_text: true,
      scenario_tags: ["negative"],
    },
  ]);
  validateCorpus(corpus);
  const report = evaluateCorpus(corpus, {
    samples: [
      { id: "table", wall_ms: 10, items: [box("타이레놀정 1정", 10), box("이부프로펜정 2정", 60)] },
      { id: "blank", wall_ms: 4, items: [] },
    ],
  });

  assert.equal(report.status, "pass");
  assert.equal(report.metrics.layout_line_recall, 1);
  assert.equal(report.metrics.layout_order_accuracy, 1);
  assert.equal(report.metrics.no_text_sample_pass_rate, 1);
  assert.equal(report.scenarios.layout.sample_count, 1);
  assert.equal(report.scenarios.negative.no_text_sample_pass_rate, 1);
  assert.equal(report.samples[0].layout_lines_found, 2);
  assert.equal(report.samples[0].layout_order_pairs_correct, 1);
});

test("reversed OCR row geometry fails the layout order gate", async () => {
  const { evaluateCorpus } = await metricsModule();
  const corpus = baseCorpus([{
    id: "rows",
    image: "rows.svg",
    expected_text: "A 1정 B 2정",
    critical_tokens: ["A", "B"],
    numeric_tokens: ["1정", "2정"],
    expected_lines: [["A", "1정"], ["B", "2정"]],
  }]);
  const report = evaluateCorpus(corpus, {
    samples: [{ id: "rows", wall_ms: 1, items: [box("A 1정", 80), box("B 2정", 20)] }],
  });

  assert.equal(report.status, "fail");
  assert.equal(report.metrics.layout_line_recall, 1);
  assert.equal(report.metrics.layout_order_accuracy, 0);
  assert.deepEqual(report.samples[0].issues, ["LAYOUT_ORDER"]);
});

test("negative controls fail visibly when OCR invents text", async () => {
  const { evaluateCorpus } = await metricsModule();
  const corpus = baseCorpus([{
    id: "blank",
    image: "blank.svg",
    expected_text: "",
    critical_tokens: [],
    numeric_tokens: [],
    expect_no_text: true,
  }]);
  const report = evaluateCorpus(corpus, {
    samples: [{ id: "blank", wall_ms: 1, items: [box("123", 10)] }],
  });

  assert.equal(report.status, "fail");
  assert.equal(report.metrics.no_text_sample_pass_rate, 0);
  assert.deepEqual(report.samples[0].issues, ["UNEXPECTED_TEXT"]);
});

test("token recall consumes repeated occurrences instead of crediting one match twice", async () => {
  const { evaluateCorpus } = await metricsModule();
  const corpus = baseCorpus([{
    id: "repeated",
    image: "repeated.svg",
    expected_text: "A 1일 B 1일",
    critical_tokens: ["A", "B"],
    numeric_tokens: ["1일", "1일"],
  }]);
  const report = evaluateCorpus(corpus, {
    samples: [{ id: "repeated", wall_ms: 1, items: [box("A 1일 B", 10)] }],
  });

  assert.equal(report.status, "fail");
  assert.equal(report.metrics.numeric_token_recall, 0.5);
  assert.deepEqual(report.samples[0].numeric_tokens_found, ["1일"]);
  assert.ok(report.samples[0].issues.includes("NUMERIC_TOKEN"));
});

test("corpus v2 rejects unsafe or malformed synthetic degradation specs", async () => {
  const { validateCorpus } = await metricsModule();
  const sample = {
    id: "stress",
    image: "stress.svg",
    expected_text: "약명 테스트정",
    critical_tokens: ["테스트정"],
    numeric_tokens: [],
    transform: { rotation_degrees: 45 },
  };
  assert.throws(() => validateCorpus(baseCorpus([sample])), /rotation_degrees/);
  sample.transform = { scale: 0.1 };
  assert.throws(() => validateCorpus(baseCorpus([sample])), /scale/);
  sample.transform = { blur_px: -1 };
  assert.throws(() => validateCorpus(baseCorpus([sample])), /blur_px/);
});
