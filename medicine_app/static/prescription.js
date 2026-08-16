
function syncPrnFields(root = document) {
  const prn = $("#pending-prn", root).checked;
  const frequency = $("#pending-frequency", root);
  const times = $("#pending-times", root);
  const maximum = $("#pending-prn-max", root);
  frequency.disabled = prn;
  times.disabled = prn;
  maximum.disabled = !prn;
  $("#fixed-schedule-fields", root)?.classList.toggle("is-disabled", prn);
  $("#prn-limit-field", root)?.classList.toggle("is-disabled", !prn);
}

function syncLongTermFields(root = document) {
  const longTerm = $("#pending-long-term", root).checked;
  const days = $("#pending-days", root);
  days.disabled = longTerm;
  $("#duration-days-field", root)?.classList.toggle("is-disabled", longTerm);
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
  const asNeeded = $("#pending-prn").checked;
  const longTerm = $("#pending-long-term").checked;
  const preservedTimes = $("#pending-times").value.split(",").map((value) => value.trim()).filter(Boolean);
  const times = asNeeded ? [] : preservedTimes;
  return {
    dose_amount: $("#pending-dose-amount").value ? Number($("#pending-dose-amount").value) : null,
    dose_unit: $("#pending-dose-unit").value.trim() || null,
    frequency_per_day: asNeeded ? null : ($("#pending-frequency").value ? Number($("#pending-frequency").value) : (times.length || null)),
    meal_relation: $("#pending-meal").value,
    administration_route: $("#pending-route").value,
    as_needed: asNeeded,
    prn_max_per_day: asNeeded && $("#pending-prn-max").value ? Number($("#pending-prn-max").value) : null,
    prescription_days: longTerm ? null : ($("#pending-days").value ? Number($("#pending-days").value) : null),
    long_term: longTerm,
    start_date: $("#pending-start-date").value || null,
    schedule_times: times,
  };
}


function applyOcrDraftToForm(draft) {
  if (!draft || typeof draft !== "object") return;
  const root = $("#risk-sheet-content");
  const unit = { tablet: "정", capsule: "캡슐", packet: "포" }[draft.dose_unit] || draft.dose_unit || "";
  if (draft.dose_amount != null) $("#pending-dose-amount", root).value = draft.dose_amount;
  if (unit) $("#pending-dose-unit", root).value = unit;
  if (draft.frequency_per_day != null) $("#pending-frequency", root).value = draft.frequency_per_day;
  if (draft.prescription_days != null) $("#pending-days", root).value = draft.prescription_days;
  if (Array.isArray(draft.schedule_times)) $("#pending-times", root).value = draft.schedule_times.join(", ");
  if (draft.meal_relation && $("#pending-meal", root).querySelector(`option[value="${CSS.escape(draft.meal_relation)}"]`)) {
    $("#pending-meal", root).value = draft.meal_relation;
  }
  if (draft.administration_route && $("#pending-route", root).querySelector(`option[value="${CSS.escape(draft.administration_route)}"]`)) {
    $("#pending-route", root).value = draft.administration_route;
  }
  if (draft.as_needed === true) $("#pending-prn", root).checked = true;
  state.warningToken = null;
  state.reviewedDraftKey = null;
  syncPrnFields(root);
  syncLongTermFields(root);
}

async function previewProduct(productRef, ocrDraft = null) {
  if (!state.currentPersonId) {
    toast("먼저 관리 대상을 추가해주세요");
    openSheet("#person-sheet");
    return;
  }
  try {
    const preview = await api(`/api/people/${state.currentPersonId}/medications/preview`, {
      method: "POST", body: JSON.stringify({ product_ref: productRef }),
    });
    state.pendingProduct = preview.product;
    state.pendingRequestId = crypto.randomUUID();
    state.pendingOcrDraft = ocrDraft && typeof ocrDraft === "object" ? { ...ocrDraft } : null;
    state.warningToken = null;
    state.reviewedDraftKey = null;
    state.editingMedicationId = null;
    renderRiskSheet(preview);
    openSheet("#risk-sheet");
  } catch (error) { toast(error.message); }
}

function durReviewOverviewHtml(items) {
  const attention = (items || []).filter((item) => ["hit", "conditional", "unknown"].includes(item.status || "unknown"));
  const clear = (items || []).filter((item) => !["hit", "conditional", "unknown"].includes(item.status || "unknown"));
  return `
    ${attention.length ? durStatusHtml(attention) : ""}
    ${clear.length ? `<details class="dur-clear-details"><summary>문제없이 확인된 DUR 항목 ${clear.length}개</summary><div class="dur-clear-list">${durStatusHtml(clear)}</div></details>` : ""}`;
}

function riskStatusHeading(preview) {
  const durChecks = preview.dur_checks || [];
  const hitCount = durChecks.filter((item) => item.status === "hit").length;
  const conditionalCount = durChecks.filter((item) => item.status === "conditional").length;
  const unknownCount = durChecks.filter((item) => item.status === "unknown").length;
  if (hitCount) return `DUR 주의사항 ${hitCount}건이 있어요`;
  if (conditionalCount) return `조건 확인이 필요한 DUR 항목 ${conditionalCount}건이 있어요`;
  if (unknownCount) return `확인이 필요한 DUR 항목 ${unknownCount}건이 있어요`;
  if (hasClearDurCoverage(preview)) return "DUR 주의사항 없음";
  return "DUR 판정 결과를 확인할 수 없어요";
}

function renderRiskSheet(preview, medication = null) {
  const root = $("#risk-sheet-content");
  const permitBlocked = !medication && preview.product.permit_status && preview.product.permit_status !== "active";
  const entryLabel = medication ? "처방 정보 수정" : "복용정보 입력";
  root.innerHTML = `
    <div class="sheet-header">
      <div><p class="eyebrow">DUR CHECK · 1/2</p><h2 id="risk-title">${escapeHtml(preview.product.product_name)}</h2></div>
      <button class="icon-button" data-close-sheet type="button" aria-label="닫기">×</button>
    </div>
    <div class="risk-summary">
      <span class="sheet-step">안전성 확인</span>
      <h2>${riskStatusHeading(preview)}</h2>
      <p class="muted small">${escapeHtml(preview.person.name)}님의 프로필, 현재 복용약 ${preview.current_medication_count}개와 중단 이력을 기준으로 확인했습니다.</p>
    </div>
    ${preview.product.permit_status && preview.product.permit_status !== "active" ? `<div class="coverage-note limited">현재 식약처 허가 상태: ${escapeHtml(permitStatusLabel(preview.product.permit_status, preview.product.permit_status_name))}${preview.product.cancel_date ? ` · ${escapeHtml(preview.product.cancel_date)}` : ""}. 현재 허가가 유효하지 않아 복용약으로 추가할 수 없습니다.</div>` : ""}
    ${reviewItemsHtml(preview.review_items || [])}
    <div>${durReviewOverviewHtml(preview.dur_checks || [])}</div>
    <div class="safety-notice">
      <strong>DUR 결과는 확인 신호예요</strong>
      <p>금기·주의 결과가 있어도 처방약을 임의로 중단하거나 변경하지 말고 의사·약사와 확인하세요.</p>
    </div>
    <div class="coverage-note"><strong>자동 확인에 포함되지 않는 정보</strong><br>알레르기, 신장·간 기능, 체중·적응증, 등록하지 않은 일반약·건강기능식품은 현재 판정에 반영하지 않습니다.</div>
    ${!medication && (preview.product.suggested_administration_route || "unknown") === "unknown" ? `<div class="coverage-note limited"><strong>투여 경로를 확인해주세요</strong><br>제품 제형만으로 사용 방법을 확정하지 못했습니다. 다음 단계에서 처방전의 투여 경로를 확인해 선택해주세요.</div>` : ""}
    <div class="risk-actions">
      <button class="secondary-button" data-close-sheet type="button">닫기</button>
      <button class="primary-button" data-open-prescription type="button" ${permitBlocked ? "disabled" : ""}>${permitBlocked ? "추가할 수 없는 품목" : entryLabel}</button>
    </div>
    <p class="risk-disclaimer">용량·투여기간은 단일 기준과 단위를 정확히 비교할 수 있을 때만 자동 판정하며, 그 밖에는 판정 불가 사유를 표시합니다.</p>`;
  $$('[data-close-sheet]', root).forEach((button) => button.addEventListener("click", closeSheets));
  $("[data-open-prescription]", root)?.addEventListener("click", () => {
    renderPrescriptionForm(preview, medication);
    if (!medication && state.pendingOcrDraft) {
      applyOcrDraftToForm(state.pendingOcrDraft);
      state.pendingOcrDraft = null;
    }
  });
}

function renderPrescriptionForm(preview, medication = null) {
  const root = $("#risk-sheet-content");
  root.innerHTML = `
    <div class="sheet-header">
      <div><p class="eyebrow">MEDICATION DETAILS · 2/2</p><h2 id="risk-title">${escapeHtml(preview.product.product_name)}</h2></div>
      <button class="icon-button" data-close-sheet type="button" aria-label="닫기">×</button>
    </div>
    <div class="entry-intro">
      <span class="sheet-step">복용정보 입력</span>
      <strong>${escapeHtml(preview.person.name)}님의 복용 방법을 확인해주세요.</strong>
      <p>처방전 또는 약 봉투에 적힌 내용을 기준으로 입력하면 오늘 일정과 DUR 정량 확인에 사용합니다.</p>
    </div>
    <div class="prescription-form">
      <section class="prescription-section">
        <div class="prescription-section-heading"><span>1</span><div><strong>복용 방법</strong><small>한 번에 사용하는 양과 투여 경로</small></div></div>
        <div class="form-grid two dose-grid">
          <label>1회 복용량<input id="pending-dose-amount" type="number" min="0" step="0.1" placeholder="1"></label>
          <label>단위<input id="pending-dose-unit" placeholder="정, mL, 포"></label>
        </div>
        <label>투여 경로
          <select id="pending-route">
            <option value="unknown">확인 필요</option>
            <option value="oral">경구</option>
            <option value="topical">외용</option>
            <option value="inhaled">흡입</option>
            <option value="ophthalmic">점안</option>
            <option value="otic">점이</option>
            <option value="nasal">비강</option>
            <option value="injection">주사</option>
            <option value="other">기타</option>
          </select>
        </label>
      </section>

      <section class="prescription-section">
        <div class="prescription-section-heading"><span>2</span><div><strong>복용 일정</strong><small>고정 일정 또는 필요시 복용 중 하나를 선택</small></div></div>
        <label class="choice-card"><input id="pending-prn" type="checkbox"><span><strong>필요할 때만 복용</strong><small>증상이 있을 때만 사용하는 약이면 선택하세요.</small></span></label>
        <div id="fixed-schedule-fields" class="dependent-fields">
          <div class="form-grid two">
            <label>하루 횟수<input id="pending-frequency" type="number" min="1" max="24" placeholder="3"></label>
            <label>식사 관계
              <select id="pending-meal">
                <option value="unspecified">미지정</option>
                <option value="after_meal">식후</option>
                <option value="before_meal">식전</option>
                <option value="with_meal">식사와 함께</option>
                <option value="empty_stomach">공복</option>
                <option value="regardless">식사 무관</option>
              </select>
            </label>
          </div>
          <label>복용 시간<input id="pending-times" placeholder="08:00, 13:00, 19:00"><small class="field-help">시간이 정해져 있다면 쉼표로 구분해 입력하세요.</small></label>
        </div>
        <div id="prn-limit-field" class="dependent-fields is-disabled">
          <label>필요시 1일 최대 횟수<input id="pending-prn-max" type="number" min="1" max="24" placeholder="예: 3"><small class="field-help">처방에 최대 횟수가 있을 때만 입력하세요.</small></label>
        </div>
      </section>

      <section class="prescription-section">
        <div class="prescription-section-heading"><span>3</span><div><strong>복용 기간</strong><small>시작일과 처방 기간</small></div></div>
        <label>복용 시작일<input id="pending-start-date" type="date"></label>
        <label class="choice-card"><input id="pending-long-term" type="checkbox"><span><strong>장기복용 · 종료일 없음</strong><small>처방 종료일이 없는 약에만 선택하세요.</small></span></label>
        <div id="duration-days-field" class="dependent-fields">
          <label>처방 일수<input id="pending-days" type="number" min="1" max="3650" placeholder="7"></label>
        </div>
      </section>
    </div>
    <div id="quantitative-warning"></div>
    <div class="risk-actions entry-actions">
      <button class="secondary-button" data-back-risk type="button">DUR 다시 보기</button>
      <button class="primary-button" id="${medication ? "confirm-edit-med" : "confirm-add-med"}" type="button">${medication ? "수정 내용 저장" : "복용약에 추가"}</button>
    </div>
    <p class="risk-disclaimer">입력값을 바꾸면 저장 시 DUR 용량·투여기간 기준을 다시 확인합니다.</p>`;

  $$('[data-close-sheet]', root).forEach((button) => button.addEventListener("click", closeSheets));
  $("[data-back-risk]", root)?.addEventListener("click", () => renderRiskSheet(preview, medication));
  if (medication) {
    $("#pending-dose-amount", root).value = medication.dose_amount ?? "";
    $("#pending-dose-unit", root).value = medication.dose_unit ?? "";
    $("#pending-frequency", root).value = medication.frequency_per_day ?? "";
    $("#pending-days", root).value = medication.prescription_days ?? "";
    $("#pending-long-term", root).checked = Boolean(medication.long_term);
    $("#pending-times", root).value = (medication.schedules || []).map((item) => item.time_of_day).join(", ");
    $("#pending-meal", root).value = medication.meal_relation || "unspecified";
    $("#pending-route", root).value = medication.administration_route || "unknown";
    $("#pending-start-date", root).value = medication.start_date || "";
    $("#pending-prn", root).checked = Boolean(medication.as_needed);
    $("#pending-prn-max", root).value = medication.prn_max_per_day ?? "";
    $("#confirm-edit-med", root).addEventListener("click", confirmEditMedication);
  } else {
    $("#pending-route", root).value = preview.product.suggested_administration_route || "unknown";
    $("#pending-start-date", root).value = todayInKorea();
    $("#confirm-add-med", root).addEventListener("click", confirmAddMedication);
  }
  $("#pending-prn", root).addEventListener("change", () => syncPrnFields(root));
  $("#pending-long-term", root).addEventListener("change", () => syncLongTermFields(root));
  syncPrnFields(root);
  syncLongTermFields(root);
}

function medicationSafetyPreview(medication) {
  const currentAssessment = medication.current_assessment || {};
  return {
    product: {
      product_name: medication.product_name,
      suggested_administration_route: medication.administration_route || "unknown",
    },
    person: currentPerson(),
    current_medication_count: Math.max((state.dashboard?.medications || []).length - 1, 0),
    risks: currentAssessment.risks || [],
    review_items: currentAssessment.review_items || [],
    dur_checks: currentAssessment.dur_checks || [],
    coverage: currentAssessment.coverage || null,
    quantitative_checks: {
      duration: currentAssessment.duration,
      dose: currentAssessment.dose,
    },
  };
}

function prepareMedicationEdit(medication) {
  state.editingMedicationId = medication.id;
  state.warningToken = null;
  state.reviewedDraftKey = null;
  state.pendingProduct = {
    product_ref: medication.catalog_item_seq || medication.product_code,
    product_name: medication.product_name,
  };
}

function openMedicationSafety(medicationId) {
  const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
  if (!medication) return;
  prepareMedicationEdit(medication);
  renderRiskSheet(medicationSafetyPreview(medication), medication);
  openSheet("#risk-sheet");
}

function openMedicationEdit(medicationId) {
  const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
  if (!medication) return;
  prepareMedicationEdit(medication);
  renderPrescriptionForm(medicationSafetyPreview(medication), medication);
  openSheet("#risk-sheet");
}

async function confirmEditMedication() {
  const medication = (state.dashboard?.medications || []).find((item) => item.id === state.editingMedicationId);
  if (!medication) return;
  const draft = prescriptionPayloadFromForm();
  try {
    await api(`/api/medications/${medication.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: medication.revision,
        ...draft,
        acknowledge_warnings: Boolean(state.warningToken),
        warning_token: state.warningToken,
      }),
    });
    state.editingMedicationId = null;
    state.warningToken = null;
    state.reviewedDraftKey = null;
    closeSheets();
    await loadDashboard();
    renderAll();
  } catch (error) {
    if (handleConfirmationRequired(error, "confirm-edit-med")) return;
    toast(error.message);
  }
}

async function confirmAddMedication() {
  if (!state.pendingProduct || !state.currentPersonId) return;
  const draft = prescriptionPayloadFromForm();
  const draftKey = JSON.stringify(draft);
  if (state.reviewedDraftKey !== draftKey) {
    let reviewRequired;
    try { reviewRequired = await reviewPrescriptionDraft(state.pendingProduct.product_ref, draft, "confirm-add-med"); }
    catch (error) { toast(error.message); return; }
    if (reviewRequired) return;
  }
  const payload = {
    product_ref: state.pendingProduct.product_ref,
    ...draft,
    request_id: state.pendingRequestId,
    acknowledge_warnings: Boolean(state.warningToken),
    warning_token: state.warningToken,
  };
  try {
    await api(`/api/people/${state.currentPersonId}/medications`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.pendingProduct = null;
    state.pendingRequestId = null;
    state.pendingOcrDraft = null;
    state.warningToken = null;
    state.reviewedDraftKey = null;
    closeSheets();
    await loadDashboard();
    renderAll();
    showScreen("meds");
  } catch (error) {
    if (handleConfirmationRequired(error, "confirm-add-med")) return;
    toast(error.message);
  }
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
