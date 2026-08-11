function friendlyErrorMessage(message) {
  const text = String(message || "");
  const exact = {
    "frequency_per_day must match the number of schedule_times": "하루 복용 횟수와 입력한 복용 시간 개수가 같아야 해요.",
    "schedule_times must not contain duplicates": "같은 복용 시간이 두 번 입력되어 있어요.",
    "schedule time must be HH:MM": "복용 시간은 08:00처럼 시:분 형식으로 입력해주세요.",
    "dose_amount must be > 0": "1회 복용량은 0보다 큰 값으로 입력해주세요.",
    "dose_amount must be a finite number": "1회 복용량을 숫자로 입력해주세요.",
    "frequency_per_day must be a positive integer": "하루 복용 횟수를 숫자로 입력해주세요.",
    "frequency_per_day must be between 1 and 24": "하루 복용 횟수는 1회에서 24회 사이로 입력해주세요.",
    "prescription_days must be a positive integer": "처방 일수를 숫자로 입력해주세요.",
    "prescription_days must be between 1 and 3650": "처방 일수는 1일 이상으로 입력해주세요.",
    "end_date conflicts with start_date and prescription_days": "복용 종료일이 시작일과 처방 일수에 맞지 않아요.",
    "end_date must be on or after start_date": "복용 종료일은 시작일보다 빠를 수 없어요.",
    "DUR product code is not linked": "제품 단위 DUR 기준을 연결하지 못했어요.",
    "ingredient duration rule is missing": "이 성분에 자동 비교 가능한 투여기간 기준이 없어요.",
    "ingredient duration rule dosage form cannot be resolved": "제품 제형을 확인하지 못해 투여기간 기준을 비교할 수 없어요.",
    "prescription duration is missing or invalid": "처방 일수를 입력하면 투여기간 기준을 비교할 수 있어요.",
    "adult dose-caution threshold is not a pediatric dose criterion": "소아 용량은 체중·나이·적응증에 따른 처방 기준이 필요해 자동 비교하지 않았어요.",
    "dose rule has multiple rows": "성분별 용량 기준이 여러 개라 자동으로 하나의 기준과 비교하지 않았어요.",
    "duration rule is missing, malformed, or ambiguous": "투여기간 기준값을 하나로 확정하지 못했어요.",
    "dose rule value or structured details are not a single numeric threshold": "용량 기준값을 하나의 수치로 확정하지 못했어요.",
  };
  return exact[text] || text;
}

function prescriptionPayloadFromForm() {
  const times = $("#pending-times").value.split(",").map((value) => value.trim()).filter(Boolean);
  return {
    dose_amount: $("#pending-dose-amount").value ? Number($("#pending-dose-amount").value) : null,
    dose_unit: $("#pending-dose-unit").value.trim() || null,
    frequency_per_day: $("#pending-frequency").value ? Number($("#pending-frequency").value) : (times.length || null),
    meal_relation: $("#pending-meal").value,
    administration_route: $("#pending-route").value,
    as_needed: $("#pending-prn").checked,
    prescription_days: $("#pending-days").value ? Number($("#pending-days").value) : null,
    start_date: $("#pending-start-date").value || null,
    schedule_times: times,
  };
}

function quantitativeCheckHtml(label, check) {
  if (!check) return "";
  if (check.result === "not_applicable" || check.coverage_only) return "";
  if (check.result === "exceeded") {
    const requested = check.requested_days ?? check.daily_amount;
    const maximum = check.maximum_days ?? check.maximum_daily_amount;
    return `<div class="risk-card warning"><strong>${label} 기준 초과</strong><p>입력값 ${escapeHtml(requested)} · 기준 ${escapeHtml(maximum)}${check.unit ? ` ${escapeHtml(check.unit)}` : ""}</p></div>`;
  }
  if (check.result === "not_evaluable") {
    return `<div class="risk-card info"><strong>${label} 자동 판정 불가</strong><p>${escapeHtml(friendlyErrorMessage(check.reason || "기준을 정확히 비교할 수 없습니다."))}</p></div>`;
  }
  return `<div class="risk-card info"><strong>${label} DUR 기준 미초과</strong></div>`;
}

function quantitativeAlertHtml(label, check) {
  return check?.result === "exceeded" ? quantitativeCheckHtml(label, check) : "";
}

function qualitativeRiskHtml(risks) {
  return (risks || []).map((risk) => `
    <div class="risk-card ${escapeHtml(risk.severity || "info")}">
      <strong>${escapeHtml(risk.title || "DUR 주의")}</strong>
      <p>${escapeHtml(risk.details || "상세 설명 없음")}</p>
      ${interactionTimingHtml(risk.timing)}
    </div>`).join("");
}

function interactionTimingHtml(timing) {
  if (!timing || timing.status !== "structured") return "";
  const amount = timing.amount ?? "";
  const unit = timing.unit || "시간";
  if (timing.kind === "minimum_separation") {
    return `<p>시간 조건: ${escapeHtml(amount)}${escapeHtml(unit)} 이내 병용금기</p>`;
  }
  if (timing.kind === "washout_after") {
    const subject = timing.subject ? `${escapeHtml(timing.subject)} ` : "해당 성분 ";
    return `<p>중단 후 주의기간: ${subject}종료 후 ${escapeHtml(amount)}${escapeHtml(unit)}</p>`;
  }
  return "";
}

function assessmentDetailsHtml(assessment) {
  const checks = assessment?.quantitative_checks || assessment || {};
  return `
    ${qualitativeRiskHtml(assessment?.risks)}
    ${quantitativeCheckHtml("투여기간", checks.duration)}
    ${quantitativeCheckHtml("1일 용량", checks.dose)}
    ${coverageLimitHtml(assessment?.coverage)}`;
}

function hasClearDurCoverage(assessment) {
  const coverage = assessment?.coverage;
  const checks = assessment?.quantitative_checks || assessment || {};
  const hasFinding = (assessment?.risks || []).length > 0;
  // not_applicable means a mapped, completed lookup found no applicable rule;
  // not_evaluable means the check could not be completed and must not look clean.
  const hasUnresolvedQuantitativeCheck = [checks.duration, checks.dose]
    .some((check) => check?.result === "exceeded" || check?.result === "not_evaluable");
  const hasCoverageGap = (coverage?.not_evaluable_checks || []).length > 0;
  return !hasFinding
    && !hasUnresolvedQuantitativeCheck
    && !hasCoverageGap
    && coverage?.status === "complete"
    && coverage?.product?.status === "matched"
    && coverage?.ingredient?.status === "matched";
}

function coverageLimitHtml(coverage) {
  const items = [...new Map((coverage?.not_evaluable_checks || []).map((item) => [`${item.category}:${item.reason}`, item])).values()];
  if (!items.length) return "";
  const labels = {
    product_mapping: "제품 단위 DUR 규칙을 연결하지 못했어요.",
    ingredient_mapping: "성분 단위 DUR 규칙을 정확히 연결하지 못했어요.",
    pregnancy_contraindication: "임신 여부가 입력되지 않아 임부금기는 확인하지 못했어요.",
    lactation_caution: "수유 여부가 입력되지 않아 수유부주의는 확인하지 못했어요.",
  };
  const mappingLabels = {
    product_mapping: "제품 단위 DUR 매핑 실패",
    ingredient_mapping: "성분 단위 DUR 매핑 실패",
  };
  const mappingItems = items.filter((item) => mappingLabels[item.category]);
  const otherItems = items.filter((item) => !mappingLabels[item.category]);
  const mappingHtml = mappingItems.map((item) => `
    <div class="risk-card warning">
      <strong>${escapeHtml(mappingLabels[item.category])}</strong>
      <p>${escapeHtml(labels[item.category] || friendlyErrorMessage(item.reason || item.category || "판정 불가"))}</p>
    </div>`).join("");
  const otherHtml = otherItems.length
    ? `<details class="coverage-details"><summary>자동 확인이 제한된 항목 ${otherItems.length}개</summary><div>${otherItems.map((item) => `<p>${escapeHtml(labels[item.category] || friendlyErrorMessage(item.reason || item.category || "판정 불가"))}</p>`).join("")}</div></details>`
    : "";
  return `${mappingHtml}${otherHtml}`;
}

async function reviewPrescriptionDraft(productRef, draft, buttonId) {
  const preview = await api(`/api/people/${state.currentPersonId}/medications/preview`, {
    method: "POST", body: JSON.stringify({ product_ref: productRef, ...draft }),
  });
  const reviewRequired = Boolean(preview.warning_token);
  state.reviewedDraftKey = JSON.stringify(draft);
  state.warningToken = preview.warning_token || null;
  $("#quantitative-warning").innerHTML = `
    <div class="coverage-note ${reviewRequired ? "limited" : "matched"}"><strong>입력한 복용 정보를 확인해주세요</strong><br>${reviewRequired ? "확인된 주의사항이나 자동 확인이 제한된 항목이 있어요. 내용을 확인한 뒤에도 저장할 수 있습니다." : "확인된 DUR 경고가 없어 바로 저장합니다."}</div>
    ${assessmentDetailsHtml(preview)}`;
  const button = $(`#${buttonId}`);
  if (button) button.textContent = reviewRequired ? "경고를 확인했고 계속 저장" : "저장 중...";
  return reviewRequired;
}

function handleConfirmationRequired(error, buttonId) {
  if (error.status !== 409 || !error.body?.confirmation_required) return false;
  state.warningToken = error.body.warning_token;
  const assessment = error.body.assessment || {};
  $("#quantitative-warning").innerHTML = `
    <div class="coverage-note limited"><strong>저장하기 전에 확인해주세요</strong><br>금기·주의 정보나 입력 기준 초과, 자동 확인이 제한된 항목이 있어요. 내용을 확인한 뒤 아래 버튼을 다시 누르면 저장됩니다.</div>
    ${assessmentDetailsHtml(assessment)}`;
  const button = $(`#${buttonId}`);
  if (button) button.textContent = "경고를 확인했고 계속 저장";
  toast("DUR 안전성 경고를 확인해주세요");
  return true;
}
