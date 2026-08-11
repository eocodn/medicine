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

test("structured interaction timing is visible in the warning details", async () => {
  const context = prescriptionContext({
    warning_token: "warning-token",
    risks: [{
      severity: "danger",
      title: "병용금기",
      details: "두 성분을 함께 사용하지 않아야 합니다.",
      timing: { status: "structured", kind: "minimum_separation", amount: 24, unit: "시간" },
    }],
    coverage: { not_evaluable_checks: [] },
    quantitative_checks: {
      duration: { result: "not_applicable" },
      dose: { result: "not_applicable" },
    },
  });

  await context.reviewPrescriptionDraft("product-1", { prescription_days: 7 }, "confirm-add-med");

  assert.match(context.$("#quantitative-warning").innerHTML, /24시간 이내 병용금기/);
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

test("clean DUR status requires complete product and ingredient mappings", () => {
  const context = prescriptionContext({});
  const clean = {
    risks: [],
    quantitative_checks: {
      duration: { result: "not_applicable" },
      dose: { result: "not_applicable" },
    },
    coverage: {
      status: "complete",
      product: { status: "matched" },
      ingredient: { status: "matched" },
    },
  };

  assert.equal(context.hasClearDurCoverage(clean), true);
  assert.equal(context.hasClearDurCoverage({
    ...clean,
    coverage: { ...clean.coverage, product: { status: "not_matched" } },
  }), false);
  assert.equal(context.hasClearDurCoverage({
    ...clean,
    coverage: { ...clean.coverage, ingredient: { status: "not_evaluable" } },
  }), false);
  assert.equal(context.hasClearDurCoverage({
    ...clean,
    coverage: { ...clean.coverage, status: "limited" },
  }), false);
  assert.equal(context.hasClearDurCoverage({
    ...clean,
    risks: [{ severity: "warning", title: "주의", details: "확인 필요" }],
  }), false);
  assert.equal(context.hasClearDurCoverage({
    ...clean,
    quantitative_checks: {
      ...clean.quantitative_checks,
      duration: { result: "not_evaluable", reason: "기간 기준 판정 불가" },
    },
  }), false);
  assert.equal(context.hasClearDurCoverage({
    ...clean,
    coverage: {
      ...clean.coverage,
      not_evaluable_checks: [{ category: "duration", reason: "일부 기준 판정 불가" }],
    },
  }), false);
});

test("mapping failures render as visible warning cards instead of collapsed details", () => {
  const context = prescriptionContext({});
  const html = context.coverageLimitHtml({
    not_evaluable_checks: [
      { category: "product_mapping", reason: "제품 매핑 실패" },
      { category: "ingredient_mapping", reason: "성분 매핑 실패" },
      { category: "dataset", reason: "데이터셋 확인 실패" },
    ],
  });

  assert.match(html, /risk-card warning/);
  assert.match(html, /제품 단위 DUR 매핑 실패/);
  assert.match(html, /성분 단위 DUR 매핑 실패/);
  const detailsIndex = html.indexOf("<details");
  assert.ok(detailsIndex > 0);
  assert.ok(html.indexOf("제품 단위 DUR 매핑 실패") < detailsIndex);
  assert.ok(html.indexOf("성분 단위 DUR 매핑 실패") < detailsIndex);
  assert.doesNotMatch(html.slice(detailsIndex), /제품 단위 DUR 매핑 실패|성분 단위 DUR 매핑 실패/);
  assert.match(html.slice(detailsIndex), /데이터셋 확인 실패/);
});

test("DUR status UI renders all eight categories with compact non-hit states", () => {
  const context = prescriptionContext({});
  const html = context.durStatusHtml([
    { category: "combination_contraindication", label: "병용금기", status: "hit", summary: "현재 복용약과 병용금기", findings: [{ severity: "danger", title: "약A와 병용금기", details: "함께 사용하지 않아야 합니다." }] },
    { category: "age_contraindication", label: "연령금기", status: "clear", summary: "해당 금기 없음", findings: [] },
    { category: "pregnancy_contraindication", label: "임부금기", status: "not_applicable", summary: "해당사항 없음", findings: [] },
    { category: "lactation_caution", label: "수유부주의", status: "not_applicable", summary: "해당사항 없음", findings: [] },
    { category: "elderly_caution", label: "노인주의", status: "not_applicable", summary: "해당사항 없음", findings: [] },
    { category: "dose_caution", label: "용량주의", status: "unknown", summary: "복용정보를 확인해주세요", findings: [] },
    { category: "duration_caution", label: "투여기간주의", status: "clear", summary: "기준 이내", findings: [] },
    { category: "therapeutic_duplication_caution", label: "효능군 중복주의", status: "clear", summary: "중복 없음", findings: [] },
  ]);

  for (const label of ["병용금기", "연령금기", "임부금기", "수유부주의", "노인주의", "용량주의", "투여기간주의", "효능군 중복주의"]) {
    assert.match(html, new RegExp(label));
  }
  assert.match(html, /dur-check hit/);
  assert.match(html, /dur-check unknown/);
  assert.match(html, /dur-check compact clear/);
  assert.match(html, /dur-check compact not_applicable/);
  assert.match(html, /함께 사용하지 않아야 합니다/);
  assert.match(html, /복용정보를 확인해주세요/);
});
