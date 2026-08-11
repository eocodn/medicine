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
    dur_checks: [{
      category: "combination_contraindication", label: "병용금기", status: "hit",
      summary: "병용금기", findings: [{ title: "병용금기", details: "함께 복용하면 안 됩니다." }],
    }],
  });

  await context.reviewPrescriptionDraft("product-1", { prescription_days: 7 }, "confirm-add-med");

  const html = context.$("#quantitative-warning").innerHTML;
  assert.match(html, /병용금기/);
  assert.match(html, /함께 복용하면 안 됩니다/);
});

test("structured interaction timing is visible in the warning details", async () => {
  const context = prescriptionContext({
    warning_token: "warning-token",
    dur_checks: [{
      category: "combination_contraindication", label: "병용금기", status: "hit",
      summary: "병용금기", findings: [{
        title: "병용금기", details: "두 성분을 함께 사용하지 않아야 합니다.",
        timing: { status: "structured", kind: "minimum_separation", amount: 24, unit: "시간" },
      }],
    }],
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
        dur_checks: [{
          category: "pregnancy_contraindication", label: "임부금기", status: "hit",
          summary: "임부금기", findings: [{ title: "임부금기", details: "임신 중 사용 금기입니다." }],
        }],
      },
    },
  }, "confirm-edit-med");

  assert.equal(handled, true);
  assert.equal(context.state.warningToken, "new-token");
  const html = context.$("#quantitative-warning").innerHTML;
  assert.match(html, /임부금기/);
  assert.match(html, /임신 중 사용 금기입니다/);
});

test("clean DUR status requires exactly eight authoritative category checks", () => {
  const context = prescriptionContext({});
  const checks = [
    ["combination_contraindication", "clear"],
    ["age_contraindication", "clear"],
    ["pregnancy_contraindication", "not_applicable"],
    ["lactation_caution", "not_applicable"],
    ["elderly_caution", "not_applicable"],
    ["dose_caution", "clear"],
    ["duration_caution", "clear"],
    ["therapeutic_duplication_caution", "clear"],
  ].map(([category, status]) => ({ category, status }));

  assert.equal(context.hasClearDurCoverage({ dur_checks: checks }), true);
  assert.equal(context.hasClearDurCoverage({
    dur_checks: checks.map((item) => item.category === "dose_caution" ? { ...item, status: "unknown" } : item),
  }), false);
  assert.equal(context.hasClearDurCoverage({
    dur_checks: checks.map((item) => item.category === "duration_caution" ? { ...item, status: "hit" } : item),
  }), false);
  assert.equal(context.hasClearDurCoverage({ dur_checks: checks.slice(0, 7) }), false);
  assert.equal(context.hasClearDurCoverage({
    dur_checks: checks,
    coverage: { status: "limited", not_evaluable_checks: [{ category: "dataset", reason: "legacy" }] },
  }), true);
});

test("authoritative DUR details never render legacy coverage output", () => {
  const context = prescriptionContext({});
  const html = context.assessmentDetailsHtml({
    dur_checks: [
      { category: "age_contraindication", label: "연령금기", status: "unknown", summary: "확인 필요", details: "제품 제형을 확정하지 못했습니다.", findings: [] },
    ],
    coverage: {
      not_evaluable_checks: [
        { category: "age_contraindication", reason: "제품 제형 정보가 없어 성분 연령금기의 제형 적용 여부를 판정할 수 없습니다." },
        { category: "dataset", reason: "데이터셋 확인 실패" },
      ],
    },
  });

  assert.match(html, /제품 제형을 확정하지 못했습니다/);
  assert.doesNotMatch(html, /자동 확인이 제한된 항목/);
  assert.doesNotMatch(html, /데이터셋 확인 실패/);
  assert.doesNotMatch(html, /제품 제형 정보가 없어 성분 연령금기/);
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

  for (const label of ["연령금기", "임부금기", "수유부주의", "노인주의", "용량주의", "투여기간주의", "효능군 중복주의"]) {
    assert.match(html, new RegExp(label));
  }
  assert.match(html, /dur-check hit/);
  const hitHtml = html.slice(html.indexOf('dur-check hit'), html.indexOf('dur-check unknown'));
  assert.doesNotMatch(hitHtml, /dur-check-heading/);
  assert.doesNotMatch(hitHtml, />병용금기</);
  assert.match(hitHtml, /약A와 병용금기/);
  assert.match(html, /dur-check unknown/);
  assert.match(html, /dur-check compact clear/);
  assert.match(html, /dur-check compact not_applicable/);
  assert.match(html, /함께 사용하지 않아야 합니다/);
  assert.match(html, /복용정보를 확인해주세요/);
});
