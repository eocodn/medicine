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
    toast: () => {},
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

test("warning preview renders qualitative DUR details before acknowledgement", async () => {
  const context = prescriptionContext({
    warning_token: "warning-token",
    risks: [{ severity: "danger", title: "병용금기", details: "함께 복용하면 안 됩니다." }],
    coverage: { not_evaluable_checks: [] },
    quantitative_checks: {
      duration: { result: "not_applicable" },
      dose: { result: "not_applicable" },
    },
  });

  await context.reviewPrescriptionDraft("product-1", { prescription_days: 7 }, "confirm-add-med");

  const html = context.$("#quantitative-warning").innerHTML;
  assert.match(html, /병용금기/);
  assert.match(html, /함께 복용하면 안 됩니다/);
});

test("authoritative confirmation response renders qualitative DUR details", () => {
  const context = prescriptionContext({});
  const handled = context.handleConfirmationRequired({
    status: 409,
    body: {
      confirmation_required: true,
      warning_token: "new-token",
      assessment: {
        risks: [{ severity: "danger", title: "임부금기", details: "임신 중 사용 금기입니다." }],
        coverage: { not_evaluable_checks: [] },
        duration: { result: "not_applicable" },
        dose: { result: "not_applicable" },
      },
    },
  }, "confirm-edit-med");

  assert.equal(handled, true);
  assert.equal(context.state.warningToken, "new-token");
  const html = context.$("#quantitative-warning").innerHTML;
  assert.match(html, /임부금기/);
  assert.match(html, /임신 중 사용 금기입니다/);
});
