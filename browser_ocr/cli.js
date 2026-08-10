#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const { parsePrescriptionHints } = require("../medicine_app/static/browser-ocr-parser.js");

const args = process.argv.slice(2);
const inputIndex = args.indexOf("--input");
if (inputIndex < 0 || !args[inputIndex + 1] || !args.includes("--json")) {
  process.stderr.write("usage: browser-ocr-inspect --input FILE|- --json\n");
  process.exitCode = 2;
} else {
  const source = args[inputIndex + 1];
  const value = source === "-" ? fs.readFileSync(0, "utf8") : fs.readFileSync(source, "utf8");
  process.stdout.write(`${JSON.stringify(parsePrescriptionHints(value))}\n`);
}
