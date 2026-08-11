const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function prescriptionContext(preview) {
  const nodes = new Map();
  const context = {
    api: async () => preview,
    state: { currentPersonId: "person-1", reviewedDraftKey: null, warningToken: null },
    escapeHtml: (value) => String(value),
    $: (selector) => {
      if (!nodes.has(selector)) nodes.set(selector, { innerHTML: "", textContent: "" });
      return nodes.get(selector);
    },
  };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.join(__dirname, "../../medicine_app/static/prescription.js"), "utf8",
  );
  vm.runInContext(source, context);
  return context;
}

test("clean preview continues registration on the same click", async () => {
  const context = prescriptionContext({
    warning_token: null,
    quantitative_checks: {
      duration: { result: "not_applicable" },
      dose: { result: "not_applicable" },
    },
  });

  const pause = await context.reviewPrescriptionDraft("product-1", { prescription_days: 7 }, "confirm-add-med");

  assert.equal(pause, false);
  assert.equal(context.state.warningToken, null);
});

test("warning preview pauses registration for a second click", async () => {
  const context = prescriptionContext({
    warning_token: "warning-token",
    quantitative_checks: {
      duration: { result: "exceeded", requested_days: 30, maximum_days: 28 },
      dose: { result: "within" },
    },
  });

  const pause = await context.reviewPrescriptionDraft("product-1", { prescription_days: 30 }, "confirm-add-med");

  assert.equal(pause, true);
  assert.equal(context.state.warningToken, "warning-token");
});
