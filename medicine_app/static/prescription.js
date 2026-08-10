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
  if (check.result === "exceeded") {
    const requested = check.requested_days ?? check.daily_amount;
    const maximum = check.maximum_days ?? check.maximum_daily_amount;
    return `<div class="risk-card warning"><strong>${label} 기준 초과</strong><p>입력값 ${escapeHtml(requested)} · 기준 ${escapeHtml(maximum)}${check.unit ? ` ${escapeHtml(check.unit)}` : ""}</p></div>`;
  }
  if (check.result === "not_evaluable") {
    return `<div class="risk-card info"><strong>${label} 자동 판정 불가</strong><p>${escapeHtml(check.reason || "기준을 정확히 비교할 수 없습니다.")}</p></div>`;
  }
  return `<div class="risk-card info"><strong>${label} 입력 기준 이내</strong></div>`;
}

async function reviewPrescriptionDraft(productRef, draft, buttonId) {
  const preview = await api(`/api/people/${state.currentPersonId}/medications/preview`, {
    method: "POST", body: JSON.stringify({ product_ref: productRef, ...draft }),
  });
  const checks = preview.quantitative_checks || {};
  const exceeded = [checks.duration, checks.dose].some((item) => item?.result === "exceeded");
  state.reviewedDraftKey = JSON.stringify(draft);
  state.warningToken = exceeded ? preview.warning_token : null;
  $("#quantitative-warning").innerHTML = `
    <div class="coverage-note ${exceeded ? "limited" : "matched"}"><strong>입력 처방 정량 판정</strong><br>${exceeded ? "DUR 기준 초과 경고를 확인한 뒤에도 등록할 수 있습니다." : "아래 결과를 확인한 뒤 같은 내용으로 저장해주세요."}</div>
    ${quantitativeCheckHtml("투여기간", checks.duration)}
    ${quantitativeCheckHtml("1일 용량", checks.dose)}`;
  const button = $(`#${buttonId}`);
  if (button) button.textContent = exceeded ? "경고를 확인했고 계속 저장" : "판정 결과를 확인했고 저장";
}

function handleConfirmationRequired(error, buttonId) {
  if (error.status !== 409 || !error.body?.confirmation_required) return false;
  state.warningToken = error.body.warning_token;
  const assessment = error.body.assessment || {};
  $("#quantitative-warning").innerHTML = `
    <div class="coverage-note limited"><strong>입력한 처방이 DUR 정량 기준을 초과합니다.</strong><br>의사·약사와 확인할 경고이며, 아래 버튼을 다시 누르면 경고 확인 이력과 함께 등록합니다.</div>
    ${quantitativeCheckHtml("투여기간", assessment.duration)}
    ${quantitativeCheckHtml("1일 용량", assessment.dose)}`;
  const button = $(`#${buttonId}`);
  if (button) button.textContent = "경고를 확인했고 계속 저장";
  toast("기준 초과 경고를 확인해주세요");
  return true;
}
