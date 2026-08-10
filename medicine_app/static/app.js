const state = {
  people: [],
  currentPersonId: localStorage.getItem("medicine.currentPersonId"),
  dashboard: null,
  fullCatalog: false,
  pendingProduct: null,
  pendingRequestId: null,
  warningToken: null,
  editingMedicationId: null,
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = `요청 실패 (${response.status})`;
    let body = null;
    try {
      body = await response.json();
      message = body.detail || message;
    } catch (_) {}
    const error = new Error(typeof message === "string" ? message : "요청을 처리하지 못했어요");
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
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

function pregnancyLabel(value) {
  return {
    pregnant: "임신 중",
    not_pregnant: "임신 중 아님",
    not_applicable: "해당 없음",
    unknown: "임신 여부 미입력",
  }[value] || value;
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
    before_meal: "식전",
    after_meal: "식후",
    with_meal: "식사와 함께",
    empty_stomach: "공복",
    regardless: "식사 무관",
    unspecified: "복용 시점 미지정",
  }[value] || value;
}

function routeLabel(value) {
  return {
    oral: "경구",
    topical: "외용",
    inhaled: "흡입",
    ophthalmic: "점안",
    otic: "점이",
    nasal: "비강",
    injection: "주사",
    other: "기타",
    unknown: "경로 미지정",
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

function renderProfileShortcut() {
  const person = currentPerson();
  $("#profile-shortcut").textContent = person ? person.name.slice(0, 1) : "+";
}

function renderHome() {
  const root = $("#home-content");
  const person = currentPerson();
  if (!person) {
    root.innerHTML = `
      <div class="hero-card">
        <p class="eyebrow">START HERE</p>
        <h2>누구의 약을<br>관리할까요?</h2>
        <p class="muted">프로필을 먼저 만들면 나이·임신 여부와 현재 복용약을 함께 확인할 수 있어요.</p>
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
      <p class="muted">현재 복용약 ${meds.length}개 · 만 ${person.age}세 · ${escapeHtml(pregnancyLabel(person.pregnancy_status))}</p>
      <div class="profile-line"><span class="profile-dot"></span><span class="small">개인 기록은 로컬 DB에 저장 중</span></div>
    </div>
    <div class="card today-card">
      <div class="today-header"><h3>오늘 복용 일정</h3><span class="count-pill">${doses.length}</span></div>
      <div class="schedule-list">
        ${doses.length ? doses.map(scheduleHtml).join("") : `<div class="empty-state"><strong>오늘 예정된 복용이 없어요</strong>처방 정보에 횟수나 시간을 입력하면 오늘 일정이 자동으로 만들어져요.</div>`}
      </div>
      ${(plan.prn_medications || []).length ? `<div class="prn-note"><strong>필요시 복용</strong>${plan.prn_medications.map((med) => escapeHtml(med.product_name)).join(" · ")}</div>` : ""}
    </div>`;

  $$('[data-instance-taken]', root).forEach((button) => button.addEventListener("click", () => completeDoseInstance(button.dataset.instanceTaken, "taken")));
}

function scheduleHtml(item) {
  const done = item.status !== "planned";
  return `
    <div class="schedule-item ${done ? "done" : ""}">
      <span class="schedule-time">${escapeHtml(item.scheduled_time || item.slot_label || "시간 미정")}</span>
      <div class="schedule-name"><strong>${escapeHtml(item.product_name)}</strong><span>${escapeHtml(item.dose_text || "복용량 미입력")}</span></div>
      ${done
        ? `<span class="dose-status ${escapeHtml(item.status)}">${item.status === "taken" ? "완료" : "건너뜀"}</span>`
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
      </div>
      <div class="med-meta">
        ${med.dosage_text ? `<span class="chip">${escapeHtml(med.dosage_text)}</span>` : ""}
        ${med.frequency_per_day ? `<span class="chip">1일 ${escapeHtml(med.frequency_per_day)}회</span>` : ""}
        ${med.as_needed ? `<span class="chip prn-chip">필요시 복용</span>` : ""}
        ${med.meal_relation && med.meal_relation !== "unspecified" ? `<span class="chip">${escapeHtml(mealRelationLabel(med.meal_relation))}</span>` : ""}
        ${med.administration_route ? `<span class="chip">${escapeHtml(routeLabel(med.administration_route))}</span>` : ""}
        ${med.prescription_days ? `<span class="chip">${escapeHtml(med.prescription_days)}일분</span>` : ""}
        ${(med.schedules || []).map((s) => `<span class="chip">${escapeHtml(s.time_of_day)}</span>`).join("")}
        ${med.catalog_item_seq && !med.product_code ? `<span class="chip caution-chip">전체 카탈로그 · DUR 매칭 없음</span>` : ""}
        ${med.source === "manual" ? `<span class="chip caution-chip">직접 입력 · DUR 제한</span>` : ""}
      </div>
      <div class="med-actions">
        <button class="secondary-button" data-edit="${med.id}" type="button">처방 수정</button>
        <button class="secondary-button" data-taken="${med.id}" type="button">복용 완료</button>
        <button class="danger-ghost" data-stop="${med.id}" type="button">복용 목록에서 종료</button>
      </div>
    </article>`).join("") : `<div class="empty-state"><strong>복용 중인 약이 없어요</strong>약 검색 탭에서 추가해보세요.</div>`;

  historyRoot.innerHTML = logs.length ? logs.map((log) => `
    <div class="history-row">
      <span class="history-icon">${log.status === "taken" ? "✓" : "–"}</span>
      <div><strong>${escapeHtml(log.product_name)}</strong><div class="history-time">${escapeHtml(log.status === "taken" ? "복용 완료" : "건너뜀")}</div></div>
      <span class="history-time">${escapeHtml(formatTime(log.occurred_at))}</span>
    </div>`).join("") : `<div class="empty-state"><strong>기록이 아직 없어요</strong>약을 복용한 뒤 완료 버튼을 눌러보세요.</div>`;

  $$('[data-taken]', medsRoot).forEach((button) => button.addEventListener("click", () => logDose(button.dataset.taken, "taken")));
  $$('[data-edit]', medsRoot).forEach((button) => button.addEventListener("click", () => openMedicationEdit(button.dataset.edit)));
  $$('[data-stop]', medsRoot).forEach((button) => button.addEventListener("click", () => stopMedication(button.dataset.stop)));
}

function renderPeople() {
  const root = $("#people-list");
  root.innerHTML = state.people.length ? state.people.map((person) => `
    <button class="card person-card" data-person="${person.id}" type="button">
      <div class="person-row">
        <span class="person-avatar">${escapeHtml(person.name.slice(0, 1))}</span>
        <div class="person-copy"><h3>${escapeHtml(person.name)}</h3><p>만 ${person.age}세 · ${escapeHtml(pregnancyLabel(person.pregnancy_status))}</p></div>
        ${person.id === state.currentPersonId ? `<span class="selected-badge">관리 중</span>` : ""}
      </div>
    </button>`).join("") : `<div class="empty-state"><strong>프로필이 없어요</strong>여러 사람의 복약을 한 기기에서 따로 관리할 수 있어요.</div>`;
  $$('[data-person]', root).forEach((button) => button.addEventListener("click", () => selectPerson(button.dataset.person)));
}

async function selectPerson(personId) {
  state.currentPersonId = personId;
  localStorage.setItem("medicine.currentPersonId", personId);
  await loadDashboard();
  renderAll();
  showScreen("home");
  toast(`${currentPerson().name}님으로 전환했어요`);
}

async function logDose(medicationId, status) {
  try {
    await api(`/api/medications/${medicationId}/logs`, { method: "POST", body: JSON.stringify({ status }) });
    await loadDashboard();
    renderAll();
    toast(status === "taken" ? "복용 완료로 기록했어요" : "건너뜀으로 기록했어요");
  } catch (error) { toast(error.message); }
}

async function completeDoseInstance(instanceId, status) {
  try {
    await api(`/api/dose-instances/${instanceId}`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    await loadDashboard();
    renderAll();
    toast(status === "taken" ? "오늘 복용 완료로 기록했어요" : "건너뜀으로 기록했어요");
  } catch (error) { toast(error.message); }
}

async function stopMedication(medicationId) {
  if (!confirm("복용 중 목록에서 종료할까요? 처방 중단 판단은 의사·약사와 확인하세요.")) return;
  try {
    const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
    const query = medication ? `?expected_revision=${medication.revision}` : "";
    await api(`/api/medications/${medicationId}${query}`, { method: "DELETE" });
    await loadDashboard();
    renderAll();
    toast("복용 목록에서 종료했어요");
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
  status.textContent = state.fullCatalog ? "전체 허가 의약품에서 찾는 중…" : "전체 허가 의약품 카탈로그를 확인하는 중…";
  try {
    const includeInactive = $("#include-inactive").checked;
    const results = await api(`/api/products?q=${encodeURIComponent(term)}&limit=30&include_inactive=${includeInactive}`);
    state.fullCatalog = true;
    status.textContent = `${results.length}개 결과 · 식약처 허가상태 + DUR 연결`;
    root.innerHTML = results.length ? results.map((item) => `
      <article class="card result-card">
        <div class="result-row">
          <div class="result-copy">
            <div class="result-title-line"><strong>${escapeHtml(item.product_name)}</strong><span class="permit-badge ${escapeHtml(item.permit_status)}">${escapeHtml(permitStatusLabel(item.permit_status, item.permit_status_name))}</span></div>
            <span>${escapeHtml(item.ingredient_name || "성분 정보 없음")}${item.manufacturer ? ` · ${escapeHtml(item.manufacturer)}` : ""}</span>
            <span>${item.dur_match ? "DUR 연결됨" : "DUR 제품코드 매칭 없음"}${item.cancel_date ? ` · 상태일 ${escapeHtml(item.cancel_date)}` : ""}</span>
          </div>
          <button class="add-button" data-add-ref="${escapeHtml(item.product_ref)}" type="button">추가</button>
        </div>
      </article>`).join("") : `<div class="empty-state"><strong>검색 결과가 없어요</strong>${state.fullCatalog ? "다른 제품명이나 성분명으로 검색해보세요." : "전체 카탈로그를 동기화하면 검색 범위를 넓힐 수 있어요."}</div>`;
    $$('[data-add-ref]', root).forEach((button) => button.addEventListener("click", () => previewProduct(button.dataset.addRef)));
  } catch (error) {
    status.textContent = error.message;
    root.innerHTML = "";
  }
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
    state.editingMedicationId = null;
    renderRiskSheet(preview);
    openSheet("#risk-sheet");
  } catch (error) { toast(error.message); }
}

function renderRiskSheet(preview, medication = null) {
  const root = $("#risk-sheet-content");
  const risks = preview.risks || [];
  const dangerous = risks.some((risk) => risk.severity === "danger");
  root.innerHTML = `
    <div class="sheet-header">
      <div><p class="eyebrow">DUR CHECK</p><h2 id="risk-title">${escapeHtml(preview.product.product_name)}</h2></div>
      <button class="icon-button" data-close-sheet type="button">×</button>
    </div>
    <div class="risk-summary">
      <h2>${medication ? "처방 정보를 수정합니다" : dangerous ? "확인이 필요한 위험이 있어요" : risks.length ? "주의 정보를 확인하세요" : preview.coverage?.dur_match ? "현재 확인된 DUR 위험 정보가 없어요" : "DUR 자동 확인 범위가 제한돼요"}</h2>
      <p class="muted small">${escapeHtml(preview.person.name)}님의 프로필과 현재 복용약 ${preview.current_medication_count}개를 기준으로 확인했습니다.</p>
    </div>
    ${preview.product.permit_status && preview.product.permit_status !== "active" ? `<div class="coverage-note limited">현재 식약처 허가 상태: ${escapeHtml(permitStatusLabel(preview.product.permit_status, preview.product.permit_status_name))}${preview.product.cancel_date ? ` · ${escapeHtml(preview.product.cancel_date)}` : ""}. 허가 상태와 실제 보유·유통 여부는 별개일 수 있어요.</div>` : ""}
    ${preview.coverage ? `<div class="coverage-note ${preview.coverage.dur_match ? "matched" : "limited"}">${escapeHtml(preview.coverage.message)}</div>` : ""}
    <div>${risks.length ? risks.map((risk) => `
      <div class="risk-card ${escapeHtml(risk.severity)}"><strong>${escapeHtml(risk.title)}</strong><p>${escapeHtml(risk.details || "상세 설명 없음")}</p></div>`).join("") : `<div class="risk-card info"><strong>DUR 결과 없음</strong><p>현재 로컬 DUR 데이터에서 일치하는 금기·주의 신호가 발견되지 않았습니다. 이것이 모든 상호작용의 부재를 뜻하지는 않습니다.</p></div>`}</div>
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
          </select>
        </label>
      </div>
      <label>복용 시작일<input id="pending-start-date" type="date"></label>
      <label class="checkbox-row"><input id="pending-prn" type="checkbox"><span><strong>필요할 때만 복용</strong><small>정해진 오늘 일정에는 넣지 않아요.</small></span></label>
    </div>
    <div id="quantitative-warning"></div>
    <div id="revision-history"></div>
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
    $("#pending-start-date", root).value = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    }).format(new Date());
    $("#confirm-add-med", root).addEventListener("click", confirmAddMedication);
  }
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

async function openMedicationEdit(medicationId) {
  const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
  if (!medication) return;
  state.editingMedicationId = medicationId;
  state.warningToken = null;
  renderRiskSheet({
    product: { product_name: medication.product_name }, person: currentPerson(),
    current_medication_count: Math.max((state.dashboard?.medications || []).length - 1, 0),
    risks: [], coverage: null,
  }, medication);
  openSheet("#risk-sheet");
  try {
    const history = await api(`/api/medications/${medicationId}/history`);
    $("#revision-history").innerHTML = history.length ? `<div class="coverage-note matched"><strong>변경 이력 ${history.length}건</strong><br>${history.map((item) => `${item.revision}판 · ${item.action}`).join(" · ")}</div>` : "";
  } catch (error) { toast(error.message); }
}

async function confirmEditMedication() {
  const medication = (state.dashboard?.medications || []).find((item) => item.id === state.editingMedicationId);
  if (!medication) return;
  try {
    await api(`/api/medications/${medication.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        expected_revision: medication.revision,
        ...prescriptionPayloadFromForm(),
        acknowledge_warnings: Boolean(state.warningToken),
        warning_token: state.warningToken,
      }),
    });
    state.editingMedicationId = null;
    state.warningToken = null;
    closeSheets();
    await loadDashboard();
    renderAll();
    toast("처방 정보를 수정했어요");
  } catch (error) {
    if (handleConfirmationRequired(error, "confirm-edit-med")) return;
    toast(error.message);
  }
}

async function confirmAddMedication() {
  if (!state.pendingProduct || !state.currentPersonId) return;
  const payload = {
    product_ref: state.pendingProduct.product_ref,
    ...prescriptionPayloadFromForm(),
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
    state.warningToken = null;
    closeSheets();
    await loadDashboard();
    renderAll();
    showScreen("meds");
    toast("복용약에 추가했어요");
  } catch (error) {
    if (handleConfirmationRequired(error, "confirm-add-med")) return;
    toast(error.message);
  }
}

async function submitPerson(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  try {
    const person = await api("/api/people", { method: "POST", body: JSON.stringify(payload) });
    state.currentPersonId = person.id;
    localStorage.setItem("medicine.currentPersonId", person.id);
    event.currentTarget.reset();
    closeSheets();
    await loadPeople();
    showScreen("home");
    toast(`${person.name}님 프로필을 만들었어요`);
  } catch (error) { toast(error.message); }
}

function bindEvents() {
  $$("[data-nav]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.nav)));
  $$("[data-go]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.go)));
  $("#profile-shortcut").addEventListener("click", () => state.people.length ? showScreen("people") : openSheet("#person-sheet"));
  $("#open-person-form").addEventListener("click", () => openSheet("#person-sheet"));
  $("#person-form").addEventListener("submit", submitPerson);
  $("#sheet-backdrop").addEventListener("click", closeSheets);
  $$('[data-close-sheet]').forEach((button) => button.addEventListener("click", closeSheets));
  $("#drug-query").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(runDrugSearch, 280);
  });
  $("#include-inactive").addEventListener("change", runDrugSearch);
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  try {
    await loadHealth();
    await loadPeople();
  }
  catch (error) { toast(`초기화 실패: ${error.message}`); }
});
