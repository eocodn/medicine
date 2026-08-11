const state = {
  people: [],
  currentPersonId: localStorage.getItem("medicine.currentPersonId"),
  dashboard: null,
  fullCatalog: false,
  pendingProduct: null,
  pendingRequestId: null,
  warningToken: null,
  reviewedDraftKey: null,
  editingMedicationId: null,
  editingPersonId: null,
  searchTimer: null,
};
const titles = {
  home: "오늘의 복약",
  meds: "복용 관리",
  search: "약 검색",
  people: "함께 관리",
  settings: "설정",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const todayInKorea = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDoseText(value) {
  const text = String(value ?? "");
  const match = text.match(/^(\d+)\.(\d+)([^\d]*)$/);
  if (!match) return text;
  const fraction = match[2].replace(/0+$/, "");
  return `${match[1]}${fraction ? `.${fraction}` : ""}${match[3]}`;
}

async function api(path, options = {}) {
  const local = window.MedicineLocalApi?.request(path, options);
  if (local !== undefined) return local;
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = "요청을 처리하지 못했어요";
    let body = null;
    try {
      body = await response.json();
      message = body.detail || message;
    } catch (_) {}
    console.error("api request failed", { path, status: response.status });
    const error = new Error(typeof message === "string" ? friendlyErrorMessage(message) : "요청을 처리하지 못했어요");
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const node = $("#toast");
  node.textContent = friendlyErrorMessage(message);
  node.classList.remove("hidden");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.add("hidden"), 2200);
}

function openSheet(selector) {
  $("#sheet-backdrop").classList.remove("hidden");
  $(selector).classList.remove("hidden");
}

function closeSheets() {
  $("#sheet-backdrop").classList.add("hidden");
  $$(".bottom-sheet").forEach((node) => node.classList.add("hidden"));
}

function showScreen(name) {
  $$(".screen").forEach((node) => node.classList.toggle("active", node.dataset.screen === name));
  $$(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.nav === name));
  $("#page-title").textContent = titles[name] || "약봄";
  if (name === "search") setTimeout(() => $("#drug-query").focus(), 80);
}

function handleOcrReviewRequired(hints, productQueries, operationId, issues) {
  showScreen("search");
  const query = MedicineOcr.renderReview(productQueries, hints?.product_name, issues);
  if (query) runDrugSearch();
}
function permitStatusLabel(value, raw) {
  return {
    active: "허가 유효",
    expired: "유효기간 만료",
    withdrawn: "취하",
    business_closed: "업체 폐업",
    canceled: "허가 취소",
    inactive_unknown: raw || "비활성",
    unknown: raw || "상태 미확인",
  }[value] || raw || value;
}
function mealRelationLabel(value) {
  return {
    before_meal: "식사 전",
    after_meal: "식사 후",
    with_meal: "식사와 함께",
    empty_stomach: "빈속에",
    regardless: "식사와 관계없이",
    unspecified: "복용 시점 미지정",
  }[value] || value;
}
function routeLabel(value) {
  return {
    oral: "먹는 약",
    topical: "바르는 약",
    inhaled: "흡입",
    ophthalmic: "안약",
    otic: "귀에 넣는 약",
    nasal: "코에 쓰는 약",
    injection: "주사",
    other: "기타",
    unknown: "사용 방법 미지정",
  }[value] || value;
}
function currentPerson() {
  return state.people.find((person) => person.id === state.currentPersonId) || null;
}
async function loadPeople() {
  state.people = await api("/api/people");
  if (!state.people.some((person) => person.id === state.currentPersonId)) {
    state.currentPersonId = state.people[0]?.id || null;
  }
  if (state.currentPersonId) {
    localStorage.setItem("medicine.currentPersonId", state.currentPersonId);
    await loadDashboard();
  } else {
    localStorage.removeItem("medicine.currentPersonId");
    state.dashboard = null;
  }
  renderAll();
}
async function loadHealth() {
  const health = await api("/api/health");
  state.fullCatalog = Boolean(health.full_catalog);
}
async function loadDashboard() {
  if (!state.currentPersonId) return;
  state.dashboard = await api(`/api/people/${state.currentPersonId}/dashboard`);
}

function renderAll() {
  renderProfileShortcut();
  renderHome();
  renderMedications();
  renderPeople();
}

function renderHome() {
  const root = $("#home-content");
  const person = currentPerson();
  if (!person) {
    root.innerHTML = `
      <div class="hero-card">
        <p class="eyebrow">START HERE</p>
        <h2>누구의 약을<br>관리할까요?</h2>
        <p class="muted">프로필을 먼저 만들면 나이와 건강 관련 정보, 현재 복용약을 함께 확인할 수 있어요.</p>
        <button class="primary-button wide" id="home-add-person" type="button">관리 대상 추가</button>
      </div>`;
    $("#home-add-person").addEventListener("click", () => openSheet("#person-sheet"));
    return;
  }

  const meds = state.dashboard?.medications || [];
  const plan = state.dashboard?.daily_plan || { doses: [], prn_medications: [], summary: {} };
  const doses = plan.doses || [];

  root.innerHTML = `
    <div class="hero-card">
      <p class="eyebrow">${escapeHtml(new Date().toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" }))}</p>
      <h2>${escapeHtml(person.name)}님의<br>복약을 챙겨볼게요.</h2>
      <p class="muted">현재 복용약 ${meds.length}개 · ${escapeHtml(profileMeta(person))}</p>
    </div>
    <div class="card today-card">
      <div class="today-header"><h3>오늘 복용 일정</h3><span class="count-pill">${doses.length}</span></div>
      <div class="schedule-list">
        ${doses.length ? doses.map(scheduleHtml).join("") : `<div class="empty-state"><strong>오늘 예정된 복용이 없어요</strong>처방 정보에 횟수나 시간을 입력하면 오늘 일정이 자동으로 만들어져요.</div>`}
      </div>
      ${(plan.prn_medications || []).length ? `<div class="prn-note"><strong>필요시 복용</strong>${plan.prn_medications.map((med) => escapeHtml(med.product_name)).join(" · ")}</div>` : ""}
    </div>`;

  $$('[data-instance-taken]', root).forEach((button) => button.addEventListener("click", () => completeDoseInstance(button.dataset.instanceTaken, "taken")));
  $$('[data-instance-cancel]', root).forEach((button) => button.addEventListener("click", () => cancelDoseInstance(button.dataset.instanceCancel)));
}

function scheduleHtml(item) {
  const done = item.status !== "planned";
  const mealRelation = item.meal_relation && item.meal_relation !== "unspecified"
    ? mealRelationLabel(item.meal_relation) : null;
  const doseMeta = [item.dose_text ? formatDoseText(item.dose_text) : "복용량 미입력", mealRelation]
    .filter(Boolean).join(" · ");
  return `
    <div class="schedule-item ${done ? "done" : ""}">
      <span class="schedule-time">${escapeHtml(item.scheduled_time || item.slot_label || "시간 미정")}</span>
      <div class="schedule-name"><strong>${escapeHtml(item.product_name)}</strong><span>${escapeHtml(doseMeta)}</span></div>
      ${item.status === "taken"
        ? `<button class="mini-action" data-instance-cancel="${item.id}" type="button">취소</button>`
        : item.status === "skipped"
          ? `<span class="dose-status skipped">건너뜀</span>`
          : `<button class="mini-action" data-instance-taken="${item.id}" type="button">먹었어요</button>`}
    </div>`;
}

function renderMedications() {
  const medsRoot = $("#medications-list");
  const historyRoot = $("#dose-history");
  const meds = state.dashboard?.medications || [];
  const logs = state.dashboard?.recent_logs || [];

  medsRoot.innerHTML = meds.length ? meds.map((med) => `
    <article class="card med-card">
      <div class="med-row">
        <div><p class="eyebrow">${escapeHtml(med.ingredient_name || "MEDICINE")}</p><h3>${escapeHtml(med.product_name)}</h3></div>
        ${med.dur_alert ? `<button class="dur-alert-badge" data-dur-alert="${med.id}" type="button" aria-label="현재 DUR 주의 항목 보기" title="현재 DUR 주의 항목 보기">!</button>` : ""}
      </div>
      ${medicationCourseHtml(med.course_progress)}
      <div class="med-meta">
        ${med.dosage_text ? `<span class="chip">한 번에 ${escapeHtml(formatDoseText(med.dosage_text))}</span>` : ""}
        ${med.frequency_per_day ? `<span class="chip">하루 ${escapeHtml(med.frequency_per_day)}회</span>` : ""}
        ${med.as_needed ? `<span class="chip prn-chip">필요할 때 복용</span>` : ""}
        ${med.meal_relation && med.meal_relation !== "unspecified" ? `<span class="chip">${escapeHtml(mealRelationLabel(med.meal_relation))}</span>` : ""}
        ${med.administration_route ? `<span class="chip">${escapeHtml(routeLabel(med.administration_route))}</span>` : ""}
        ${med.prescription_days && !med.course_progress ? `<span class="chip">${escapeHtml(med.prescription_days)}일 복용</span>` : ""}
        ${(med.schedules || []).map((s) => `<span class="chip">매일 ${escapeHtml(s.time_of_day)}</span>`).join("")}
        ${med.source === "manual" ? `<span class="chip caution-chip">직접 입력 · DUR 제한</span>` : ""}
      </div>
      <div class="med-actions">
        <button class="secondary-button" data-edit="${med.id}" type="button">처방 수정</button>
        <button class="danger-ghost" data-stop="${med.id}" type="button">삭제</button>
      </div>
    </article>`).join("") : `<div class="empty-state"><strong>복용 중인 약이 없어요</strong>약 검색 탭에서 추가해보세요.</div>`;

  historyRoot.innerHTML = logs.length ? logs.map((log) => `
    <div class="history-row">
      <span class="history-icon">${log.status === "taken" ? "✓" : "–"}</span>
      <div><strong>${escapeHtml(log.product_name)}</strong><div class="history-time">${escapeHtml(log.status === "taken" ? "복용 완료" : "건너뜀")}</div></div>
      <span class="history-time">${escapeHtml(formatTime(log.occurred_at))}</span>
    </div>`).join("") : `<div class="empty-state"><strong>기록이 아직 없어요</strong>약을 복용한 뒤 완료 버튼을 눌러보세요.</div>`;

  $$('[data-dur-alert]', medsRoot).forEach((button) => button.addEventListener("click", () => openMedicationEdit(button.dataset.durAlert)));
  $$('[data-edit]', medsRoot).forEach((button) => button.addEventListener("click", () => openMedicationEdit(button.dataset.edit)));
  $$('[data-stop]', medsRoot).forEach((button) => button.addEventListener("click", () => stopMedication(button.dataset.stop)));
}

async function completeDoseInstance(instanceId, status) {
  try {
    await api(`/api/dose-instances/${instanceId}`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    await loadDashboard();
    renderAll();
  } catch (error) { toast(error.message); }
}

async function cancelDoseInstance(instanceId) {
  try {
    await api(`/api/dose-instances/${instanceId}/completion`, { method: "DELETE" });
    await loadDashboard();
    renderAll();
  } catch (error) { toast(error.message); }
}

async function stopMedication(medicationId) {
  if (!confirm("복용약 목록에서 삭제할까요? 기존 복용 기록은 남습니다.")) return;
  try {
    const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
    const query = medication ? `?expected_revision=${medication.revision}` : "";
    await api(`/api/medications/${medicationId}${query}`, { method: "DELETE" });
    await loadDashboard();
    renderAll();
  } catch (error) { toast(error.message); }
}

function formatTime(value) {
  try {
    return new Date(value).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch (_) { return value; }
}

async function runDrugSearch() {
  const term = $("#drug-query").value.trim();
  const status = $("#search-status");
  const root = $("#drug-results");
  if (!term) { status.textContent = ""; root.innerHTML = ""; return; }
  status.textContent = "";
  try {
    const includeInactive = $("#include-inactive").checked;
    const results = await api(`/api/products?q=${encodeURIComponent(term)}&limit=30&include_inactive=${includeInactive}`);
    state.fullCatalog = true;
    status.textContent = "";
    root.innerHTML = results.length ? results.map((item) => `
      <article class="card result-card" data-product-select="${escapeHtml(item.product_ref)}" role="button" tabindex="0">
        <div class="result-row">
          <div class="result-copy">
            <div class="result-title-line"><strong>${escapeHtml(item.product_name)}</strong><span class="permit-badge ${escapeHtml(item.permit_status)}">${escapeHtml(permitStatusLabel(item.permit_status, item.permit_status_name))}</span></div>
            <span>${escapeHtml(item.ingredient_name || "성분 정보 없음")}${item.manufacturer ? ` · ${escapeHtml(item.manufacturer)}` : ""}</span>
            <span>${item.dur_match ? "DUR 자동 확인 가능" : "DUR 자동 확인 일부 제한"}${item.cancel_date ? ` · 허가 상태 변경일 ${escapeHtml(item.cancel_date)}` : ""}</span>
          </div>
          <span class="add-button" aria-hidden="true">추가</span>
        </div>
      </article>`).join("") : `<div class="empty-state"><strong>검색 결과가 없어요</strong>다른 제품명이나 성분명으로 검색해보세요.</div>`;
    $$('[data-product-select]', root).forEach((card) => {
      card.addEventListener("click", () => selectProductResult(card));
      card.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        selectProductResult(card);
      });
    });
  } catch (error) {
    status.textContent = friendlyErrorMessage(error.message);
    root.innerHTML = "";
  }
}

function selectProductResult(card) {
  previewProduct(card.dataset.productSelect);
}

async function previewProduct(productRef) {
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
    state.warningToken = null;
    state.reviewedDraftKey = null;
    state.editingMedicationId = null;
    renderRiskSheet(preview, null, MedicineOcr.getReview()?.hints);
    openSheet("#risk-sheet");
  } catch (error) { toast(error.message); }
}

function renderRiskSheet(preview, medication = null, ocrHints = null) {
  const root = $("#risk-sheet-content");
  const risks = preview.risks || [];
  const checks = preview.quantitative_checks || {};
  const durChecks = preview.dur_checks || [];
  const hitCount = durChecks.filter((item) => item.status === "hit").length;
  const unknownCount = durChecks.filter((item) => item.status === "unknown").length;
  const dangerous = risks.some((risk) => risk.severity === "danger" && risk.evaluation_status !== "unknown");
  const quantitativeAlert = [checks.duration, checks.dose].some((check) => check?.result === "exceeded");
  const hasDurFinding = durChecks.length ? hitCount > 0 : risks.length > 0 || quantitativeAlert;
  const clearDurCoverage = hasClearDurCoverage(preview);
  const statusHeading = medication
    ? "처방 정보를 수정합니다"
    : hitCount
      ? `DUR 주의사항 ${hitCount}건이 있어요`
      : unknownCount
        ? `확인이 필요한 DUR 항목 ${unknownCount}건이 있어요`
        : dangerous
          ? "확인이 필요한 위험이 있어요"
          : hasDurFinding
            ? "주의 정보를 확인하세요"
            : clearDurCoverage
              ? "현재 확인된 DUR 주의사항이 없어요"
              : "현재 확인된 위험은 없지만 일부 항목은 확인이 필요해요";
  root.innerHTML = `
    <div class="sheet-header">
      <div><p class="eyebrow">DUR CHECK</p><h2 id="risk-title">${escapeHtml(preview.product.product_name)}</h2></div>
      <button class="icon-button" data-close-sheet type="button">×</button>
    </div>
    <div class="risk-summary">
      <h2>${statusHeading}</h2>
      <p class="muted small">${escapeHtml(preview.person.name)}님의 프로필, 현재 복용약 ${preview.current_medication_count}개와 중단 이력을 기준으로 확인했습니다.</p>
    </div>
    ${preview.product.permit_status && preview.product.permit_status !== "active" ? `<div class="coverage-note limited">현재 식약처 허가 상태: ${escapeHtml(permitStatusLabel(preview.product.permit_status, preview.product.permit_status_name))}${preview.product.cancel_date ? ` · ${escapeHtml(preview.product.cancel_date)}` : ""}. 허가 상태와 실제 보유·유통 여부는 별개일 수 있어요.</div>` : ""}
    <div>${durChecks.length
      ? durStatusHtml(durChecks)
      : risks.length
        ? qualitativeRiskHtml(risks)
        : quantitativeAlert
          ? ""
          : clearDurCoverage
            ? `<div class="risk-card info"><strong>현재 확인된 DUR 위험 없음</strong><p>확인 가능한 DUR 범위에서 일치하는 금기·주의 신호가 발견되지 않았어요.</p></div>`
            : ""}</div>
    ${durChecks.length ? "" : quantitativeAlertHtml("투여기간", checks.duration)}
    ${durChecks.length ? "" : quantitativeAlertHtml("1일 용량", checks.dose)}
    ${coverageLimitHtml(preview.coverage)}
    <div class="coverage-note"><strong>DUR 자동 확인에 포함되지 않는 정보</strong><br>알레르기, 신장·간 기능, 체중·적응증, 등록하지 않은 일반약·건강기능식품은 현재 판정에 반영하지 않습니다.</div>
    <div class="prescription-form">
      <div class="form-grid two">
        <label>1회 복용량<input id="pending-dose-amount" type="number" min="0" step="0.1" placeholder="1"></label>
        <label>단위<input id="pending-dose-unit" placeholder="정, mL, 포"></label>
      </div>
      <div class="form-grid two">
        <label>1일 횟수<input id="pending-frequency" type="number" min="1" max="24" placeholder="3"></label>
        <label>처방 일수<input id="pending-days" type="number" min="1" max="3650" placeholder="7"></label>
      </div>
      <label>복용 시간<input id="pending-times" placeholder="예: 08:00, 13:00, 19:00"></label>
      <div class="form-grid two">
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
        <label>투여 경로
          <select id="pending-route">
            <option value="oral">경구</option>
            <option value="topical">외용</option>
            <option value="inhaled">흡입</option>
            <option value="ophthalmic">점안</option>
            <option value="nasal">비강</option>
            <option value="injection">주사</option>
            <option value="other">기타</option>
            <option value="unknown">미지원·확인 필요</option>
          </select>
        </label>
      </div>
      <label>복용 시작일<input id="pending-start-date" type="date"></label>
      <label class="checkbox-row"><input id="pending-prn" type="checkbox"><span><strong>필요할 때만 복용</strong><small>정해진 오늘 일정에는 넣지 않아요.</small></span></label>
    </div>
    <div id="ocr-issue-warning"></div>
    <div id="quantitative-warning"></div>
    <div class="risk-actions">
      <button class="secondary-button" data-close-sheet type="button">취소</button>
      <button class="primary-button" id="${medication ? "confirm-edit-med" : "confirm-add-med"}" type="button">${medication ? "수정 내용 저장" : "복용약에 추가"}</button>
    </div>
    <p class="risk-disclaimer">DUR 금기·주의는 의료진 확인을 위한 안전 신호입니다. 결과를 근거로 처방약을 임의 중단하거나 변경하지 마세요.</p>`;
  $$('[data-close-sheet]', root).forEach((button) => button.addEventListener("click", closeSheets));
  if (medication) {
    $("#pending-dose-amount", root).value = medication.dose_amount ?? "";
    $("#pending-dose-unit", root).value = medication.dose_unit ?? "";
    $("#pending-frequency", root).value = medication.frequency_per_day ?? "";
    $("#pending-days", root).value = medication.prescription_days ?? "";
    $("#pending-times", root).value = (medication.schedules || []).map((item) => item.time_of_day).join(", ");
    $("#pending-meal", root).value = medication.meal_relation || "unspecified";
    $("#pending-route", root).value = medication.administration_route || "oral";
    $("#pending-start-date", root).value = medication.start_date || "";
    $("#pending-prn", root).checked = Boolean(medication.as_needed);
    $("#confirm-edit-med", root).addEventListener("click", confirmEditMedication);
  } else {
    $("#pending-start-date", root).value = todayInKorea();
    $("#confirm-add-med", root).addEventListener("click", confirmAddMedication);
    MedicineOcr.prefillForm(ocrHints, MedicineOcr.getReview()?.issues);
  }
}

function openMedicationEdit(medicationId) {
  const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
  if (!medication) return;
  state.editingMedicationId = medicationId;
  state.warningToken = null;
  state.reviewedDraftKey = null;
  state.pendingProduct = {
    product_ref: medication.catalog_item_seq || medication.product_code,
    product_name: medication.product_name,
  };
  const currentAssessment = medication.current_assessment || {};
  renderRiskSheet({
    product: { product_name: medication.product_name }, person: currentPerson(),
    current_medication_count: Math.max((state.dashboard?.medications || []).length - 1, 0),
    risks: currentAssessment.risks || [],
    dur_checks: currentAssessment.dur_checks || [],
    coverage: currentAssessment.coverage || null,
    quantitative_checks: {
      duration: currentAssessment.duration,
      dose: currentAssessment.dose,
    },
  }, medication);
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
  const ocrReview = MedicineOcr.getReview();
  if (state.reviewedDraftKey !== draftKey) {
    if (ocrReview) {
      const envelope = { version: 1, operation_id: ocrReview.operation_id, hints: { ...ocrReview.hints, ...draft, product_ref: state.pendingProduct.product_ref } };
      try {
        const reviewed = await api(`/api/people/${state.currentPersonId}/medications/ocr-preview`, { method: "POST", body: JSON.stringify(envelope) });
        MedicineOcr.setReviewToken(reviewed.ocr_review_token);
      } catch (error) { toast(error.message); return; }
    }
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
    ...(ocrReview ? { source: "ocr", ocr_origin: true, ocr_review_token: ocrReview.review_token } : {}),
  };
  try {
    await api(`/api/people/${state.currentPersonId}/medications`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (ocrReview) MedicineOcr.finish();
    state.pendingProduct = null;
    state.pendingRequestId = null;
    state.warningToken = null;
    state.reviewedDraftKey = null;
    closeSheets();
    await loadDashboard();
    renderAll();
    showScreen("meds");
  } catch (error) {
    if (handleConfirmationRequired(error, "confirm-add-med")) return;
    if (ocrReview && error.status === 400) {
      MedicineOcr.clearReviewToken();
      state.reviewedDraftKey = null;
      toast("사진에서 불러온 처방 확인 시간이 지났어요. 내용을 다시 확인해주세요.");
      return;
    }
    toast(error.message);
  }
}

function bindEvents() {
  $$("[data-nav]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.nav)));
  $$("[data-go]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.go)));
  bindPeopleEvents();
  $("#sheet-backdrop").addEventListener("click", closeSheets);
  $$('[data-close-sheet]').forEach((button) => button.addEventListener("click", closeSheets));
  $("#drug-query").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(runDrugSearch, 280);
  });
  $("#include-inactive").addEventListener("change", runDrugSearch);
  $("#ocr-scan-button").addEventListener("click", MedicineOcr.toggle);
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  MedicineOcr.init({ onReviewRequired: handleOcrReviewRequired, onState: MedicineOcr.renderState });
  try {
    await loadHealth();
    await loadPeople();
    const requestedScreen = new URLSearchParams(window.location.search).get("screen");
    if (requestedScreen && Object.hasOwn(titles, requestedScreen)) showScreen(requestedScreen);
  }
  catch (error) {
    console.error("app initialization failed", error);
    toast("앱을 불러오지 못했어요. 다시 열어주세요.");
  }
});
