"use strict";

const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");
const assert = require("node:assert/strict");

const cli = path.join(__dirname, "../mobile/cli.mjs");

test("mobile OCR agent-control CLI reports selected model identity as JSON", () => {
  const result = spawnSync(process.execPath, [cli, "inspect", "--json"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const body = JSON.parse(result.stdout);
  assert.equal(body.status, "ok");
  assert.equal(body.model_id, "medicine-ppocrv5-full-document-only-e4-onnx-2026-08-16");
  assert.equal(body.selected_checkpoint_sha256, "804a57a050096ed34feda8cfa584b1e335685c2703494341cc74ce933c1ee12f");
  assert.equal(body.recognizer_sha256_valid, true);
  assert.equal(body.dictionary_sha256_valid, true);
  assert.deepEqual(body.parser, { enabled: false });
});
