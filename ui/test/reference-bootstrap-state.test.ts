"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

test("reference unavailable is a usable personal-data bootstrap terminal state", () => {
  const source = fs.readFileSync(path.join(__dirname, "../../ui/src/reference-bootstrap.ts"), "utf8");
  assert.match(source, /current\.state === "ready"[^\n]*\|\|[^\n]*current\.state === "unavailable"/);
});
