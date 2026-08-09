const state = {
  people: [],
  currentPersonId: localStorage.getItem("medicine.currentPersonId"),
  dashboard: null,
  pendingProduct: null,
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
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch (_) {}
    throw new Error(message);
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
  const schedules = meds.flatMap((med) =>
    (med.schedules || []).map((schedule) => ({ ...schedule, medication: med }))
  ).sort((a, b) => a.time_of_day.localeCompare(b.time_of_day));

  root.innerHTML = `
    <div class="hero-card">
      <p class="eyebrow">${escapeHtml(new Date().toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" }))}</p>
      <h2>${escapeHtml(person.name)}님의<br>복약을 챙겨볼게요.</h2>
      <p class="muted">현재 복용약 ${meds.length}개 · 만 ${person.age}세 · ${escapeHtml(pregnancyLabel(person.pregnancy_status))}</p>
      <div class="profile-line"><span class="profile-dot"></span><span class="small">개인 기록은 로컬 DB에 저장 중</span></div>
    </div>
    <div class="card today-card">
      <div class="today-header"><h3>오늘 복용 일정</h3><span class="count-pill">${schedules.length}</span></div>
      <div class="schedule-list">
        ${schedules.length ? schedules.map(scheduleHtml).join("") : `<div class="empty-state"><strong>아직 일정이 없어요</strong>약을 추가하면서 복용 시간을 설정해보세요.</div>`}
      </div>
    </div>`;

  $$('[data-log-taken]', root).forEach((button) => button.addEventListener("click", () => logDose(button.dataset.logTaken, "taken")));
}

function scheduleHtml(item) {
  const med = item.medication;
  return `
    <div class="schedule-item">
      <span class="schedule-time">${escapeHtml(item.time_of_day)}</span>
      <div class="schedule-name"><strong>${escapeHtml(med.product_name)}</strong><span>${escapeHtml(item.dose_text || med.dosage_text || "복용량 미입력")}</span></div>
      <button class="mini-action" data-log-taken="${med.id}" type="button">먹었어요</button>
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
        ${(med.schedules || []).map((s) => `<span class="chip">${escapeHtml(s.time_of_day)}</span>`).join("")}
        ${med.source === "manual" ? `<span class="chip">직접 입력 · DUR 제한</span>` : ""}
      </div>
      <div class="med-actions">
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

async function stopMedication(medicationId) {
  if (!confirm("복용 중 목록에서 종료할까요? 처방 중단 판단은 의사·약사와 확인하세요.")) return;
  try {
    await api(`/api/medications/${medicationId}`, { method: "DELETE" });
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
  status.textContent = "DUR 수록 제품에서 찾는 중…";
  try {
    const results = await api(`/api/products?q=${encodeURIComponent(term)}&limit=30`);
    status.textContent = `${results.length}개 결과 · 현재는 DUR 수록 제품 기준`;
    root.innerHTML = results.length ? results.map((item) => `
      <article class="card result-card">
        <div class="result-row">
          <div class="result-copy"><strong>${escapeHtml(item.product_name)}</strong><span>${escapeHtml(item.ingredient_name || "성분 정보 없음")} · ${escapeHtml(item.product_code)}</span></div>
          <button class="add-button" data-add-code="${escapeHtml(item.product_code)}" type="button">추가</button>
        </div>
      </article>`).join("") : `<div class="empty-state"><strong>검색 결과가 없어요</strong>현재 DB는 DUR 수록 제품 기준이라 전체 허가 의약품 검색은 다음 데이터 확장에서 보완할 예정이에요.</div>`;
    $$('[data-add-code]', root).forEach((button) => button.addEventListener("click", () => previewProduct(button.dataset.addCode)));
  } catch (error) {
    status.textContent = error.message;
    root.innerHTML = "";
  }
}

async function previewProduct(productCode) {
  if (!state.currentPersonId) {
    toast("먼저 관리 대상을 추가해주세요");
    openSheet("#person-sheet");
    return;
  }
  try {
    const preview = await api(`/api/people/${state.currentPersonId}/medications/preview`, {
      method: "POST", body: JSON.stringify({ product_code: productCode }),
    });
    state.pendingProduct = preview.product;
    renderRiskSheet(preview);
    openSheet("#risk-sheet");
  } catch (error) { toast(error.message); }
}

function renderRiskSheet(preview) {
  const root = $("#risk-sheet-content");
  const risks = preview.risks || [];
  const dangerous = risks.some((risk) => risk.severity === "danger");
  root.innerHTML = `
    <div class="sheet-header">
      <div><p class="eyebrow">DUR CHECK</p><h2 id="risk-title">${escapeHtml(preview.product.product_name)}</h2></div>
      <button class="icon-button" data-close-sheet type="button">×</button>
    </div>
    <div class="risk-summary">
      <h2>${dangerous ? "확인이 필요한 위험이 있어요" : risks.length ? "주의 정보를 확인하세요" : "현재 확인된 DUR 위험이 없어요"}</h2>
      <p class="muted small">${escapeHtml(preview.person.name)}님의 프로필과 현재 복용약 ${preview.current_medication_count}개를 기준으로 확인했습니다.</p>
    </div>
    <div>${risks.length ? risks.map((risk) => `
      <div class="risk-card ${escapeHtml(risk.severity)}"><strong>${escapeHtml(risk.title)}</strong><p>${escapeHtml(risk.details || "상세 설명 없음")}</p></div>`).join("") : `<div class="risk-card info"><strong>DUR 결과 없음</strong><p>현재 로컬 DUR 데이터에서 일치하는 금기·주의 신호가 발견되지 않았습니다. 이것이 모든 상호작용의 부재를 뜻하지는 않습니다.</p></div>`}</div>
    <div class="form-stack">
      <label>복용량 메모<input id="pending-dose" placeholder="예: 1정"></label>
      <label>복용 시간<input id="pending-times" placeholder="예: 08:00, 20:00"></label>
    </div>
    <div class="risk-actions">
      <button class="secondary-button" data-close-sheet type="button">취소</button>
      <button class="primary-button" id="confirm-add-med" type="button">복용약에 추가</button>
    </div>
    <p class="risk-disclaimer">DUR 금기·주의는 의료진 확인을 위한 안전 신호입니다. 결과를 근거로 처방약을 임의 중단하거나 변경하지 마세요.</p>`;
  $$('[data-close-sheet]', root).forEach((button) => button.addEventListener("click", closeSheets));
  $("#confirm-add-med", root).addEventListener("click", confirmAddMedication);
}

async function confirmAddMedication() {
  if (!state.pendingProduct || !state.currentPersonId) return;
  const dosage = $("#pending-dose").value.trim() || null;
  const times = $("#pending-times").value.split(",").map((value) => value.trim()).filter(Boolean);
  try {
    await api(`/api/people/${state.currentPersonId}/medications`, {
      method: "POST",
      body: JSON.stringify({ product_code: state.pendingProduct.product_code, dosage_text: dosage, schedule_times: times }),
    });
    state.pendingProduct = null;
    closeSheets();
    await loadDashboard();
    renderAll();
    showScreen("meds");
    toast("복용약에 추가했어요");
  } catch (error) { toast(error.message); }
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
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  try { await loadPeople(); }
  catch (error) { toast(`초기화 실패: ${error.message}`); }
});
