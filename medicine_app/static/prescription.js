
function syncPrnFields(root = document) {
  const prn = $("#pending-prn", root).checked;
  const frequency = $("#pending-frequency", root);
  const times = $("#pending-times", root);
  const maximum = $("#pending-prn-max", root);
  if (prn) { frequency.value = ""; times.value = ""; }
  frequency.disabled = prn;
  times.disabled = prn;
  maximum.disabled = !prn;
  if (!prn) maximum.value = "";
}

function syncLongTermFields(root = document) {
  const longTerm = $("#pending-long-term", root).checked;
  const days = $("#pending-days", root);
  if (longTerm) days.value = "";
  days.disabled = longTerm;
}

async function recordPrnIntake(medicationId) {
  try {
    await api(`/api/medications/${medicationId}/prn-intakes`, { method: "POST", body: "{}" });
    await loadDashboard();
    renderAll();
    toast("필요시 복용을 기록했어요");
  } catch (error) { toast(error.message); }
}

function friendlyErrorMessage(message) {
  const text = String(message || "");
  const exact = {
    "frequency_per_day must match the number of schedule_times": "하루 복용 횟수와 입력한 복용 시간 개수가 같아야 해요.",
    "schedule_times must not contain duplicates": "같은 복용 시간이 두 번 입력되어 있어요.",
    "prescription duration or explicit long_term mode is required": "처방 일수를 입력하거나 장기복용·종료일 없음을 선택해주세요.",
    "PRN/as_needed medication cannot have a fixed daily frequency or schedule": "필요시 복용약에는 고정 횟수나 복용 시간을 함께 설정할 수 없어요.",
    "inactive permit product cannot be added to the current medication regimen": "현재 허가가 유효하지 않은 제품은 복용약으로 추가할 수 없어요.",
    "PRN daily maximum has already been reached": "오늘 설정한 필요시 최대 복용 횟수에 도달했어요.",
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
    prn_max_per_day: $("#pending-prn-max").value ? Number($("#pending-prn-max").value) : null,
    prescription_days: $("#pending-days").value ? Number($("#pending-days").value) : null,
    long_term: $("#pending-long-term").checked,
    start_date: $("#pending-start-date").value || null,
    schedule_times: times,
  };
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


function durStatusHtml(items) {
  return (items || []).map((item) => {
    const status = item.status || "unknown";
    const label = escapeHtml(item.label || item.category || "DUR 항목");
    const summary = escapeHtml(item.summary || "확인 필요");
    const findings = item.findings || [];
    if (status === "hit") {
      const detailHtml = findings.length
        ? findings.map((finding) => `
          <div class="dur-finding">
            <strong>${escapeHtml(finding.title || item.summary || "DUR 주의사항")}</strong>
            <p>${escapeHtml(finding.details || item.details || "상세 설명 없음")}</p>
            ${interactionTimingHtml(finding.timing)}
          </div>`).join("")
        : `<div class="dur-finding"><strong>${summary}</strong>${item.details ? `<p>${escapeHtml(item.details)}</p>` : ""}</div>`;
      return `<section class="dur-check hit">${detailHtml}</section>`;
    }
    if (status === "conditional") {
      const detailHtml = findings.length
        ? findings.map((finding) => `
          <div class="dur-finding">
            <strong>${escapeHtml(finding.title || item.summary || "DUR 조건 확인 필요")}</strong>
            <p>${escapeHtml(finding.details || item.details || "규칙의 적용 조건을 확인해야 합니다.")}</p>
            ${interactionTimingHtml(finding.timing)}
          </div>`).join("")
        : (item.details ? `<p>${escapeHtml(item.details)}</p>` : "");
      return `<section class="dur-check conditional"><div class="dur-check-heading"><strong>${label}</strong><span>${summary}</span></div>${detailHtml}</section>`;
    }
    if (status === "unknown") {
      return `<section class="dur-check unknown"><div class="dur-check-heading"><strong>${label}</strong><span>${summary}</span></div>${item.details ? `<p>${escapeHtml(item.details)}</p>` : ""}</section>`;
    }
    return `<div class="dur-check compact ${escapeHtml(status)}"><strong>${label}</strong><span>${summary}</span></div>`;
  }).join("");
}

function reviewItemsHtml(items) {
  return (items || []).map((item) => `
    <section class="coverage-note limited review-item">
      <strong>${escapeHtml(item.title || "추가 확인 필요")}</strong>
      ${item.details ? `<br>${escapeHtml(item.details)}` : ""}
    </section>`).join("");
}

function assessmentDetailsHtml(assessment) {
  return `${reviewItemsHtml(assessment?.review_items || [])}${durStatusHtml(assessment?.dur_checks || [])}`;
}

function hasClearDurCoverage(assessment) {
  const durChecks = assessment?.dur_checks || [];
  return durChecks.length === 8
    && durChecks.every((item) => item.status === "clear" || item.status === "not_applicable");
}

async function reviewPrescriptionDraft(productRef, draft, buttonId) {
  const preview = await api(`/api/people/${state.currentPersonId}/medications/preview`, {
    method: "POST", body: JSON.stringify({ product_ref: productRef, ...draft }),
  });
  const reviewRequired = Boolean(preview.warning_token);
  state.reviewedDraftKey = JSON.stringify(draft);
  state.warningToken = preview.warning_token || null;
  $("#quantitative-warning").innerHTML = `
    <div class="coverage-note ${reviewRequired ? "limited" : "matched"}"><strong>입력한 복용 정보를 확인해주세요</strong><br>${reviewRequired ? "확인된 주의사항이나 추가 확인 항목이 있어요. 내용을 확인한 뒤에도 저장할 수 있습니다." : "확인된 DUR 경고가 없어 바로 저장합니다."}</div>
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
    <div class="coverage-note limited"><strong>저장하기 전에 확인해주세요</strong><br>금기·주의 정보나 추가 확인 항목이 있어요. 내용을 확인한 뒤 아래 버튼을 다시 누르면 저장됩니다.</div>
    ${assessmentDetailsHtml(assessment)}`;
  const button = $(`#${buttonId}`);
  if (button) button.textContent = "경고를 확인했고 계속 저장";
  toast("DUR 안전성 경고를 확인해주세요");
  return true;
}
