const titles = {
    home: "오늘의 복약",
    meds: "복용 관리",
    search: "약 검색",
    people: "프로필",
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
    if (!match)
        return text;
    const fraction = match[2].replace(/0+$/, "");
    return `${match[1]}${fraction ? `.${fraction}` : ""}${match[3]}`;
}
async function api(path, options = {}) {
    const local = window.MedicineLocalApi?.request(path, options);
    if (local !== undefined)
        return local;
    const { coalesceKey: _coalesceKey, ...fetchOptions } = options;
    let response;
    try {
        response = await fetch(path, {
            ...fetchOptions,
            headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) },
        });
    }
    catch (error) {
        console.error("api transport failed", { path, error });
        throw new Error("요청을 처리하지 못했어요");
    }
    if (!response.ok) {
        let message = "요청을 처리하지 못했어요";
        let body = null;
        try {
            body = await response.json();
            message = body.detail || message;
        }
        catch (_) { }
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
    node.textContent = String(message || "");
    node.classList.remove("hidden");
    clearTimeout(node._timer);
    node._timer = setTimeout(() => node.classList.add("hidden"), 2200);
}
function invalidateProductSearch() {
    state.searchRequestId += 1;
    clearTimeout(state.searchTimer);
    state.searchTimer = null;
    state.searchObserver?.disconnect?.();
    state.searchObserver = null;
    state.searchTerm = null;
    state.searchNextOffset = null;
    state.searchHasMore = false;
    state.searchLoadingMore = false;
}
function resetParserTransientState({ clearSearch = false } = {}) {
    const hadParserSearch = Boolean(state.activeParserRow || state.pendingParserRows.length || state.pendingParserDraft);
    state.pendingParserDraft = null;
    state.pendingParserPersonId = null;
    state.pendingParserUncertaintyCodes = [];
    state.pendingParserRows = [];
    state.activeParserRow = null;
    state.parserRowTotal = 0;
    state.parserRowIndex = 0;
    window.MedicineOcrIntake?.reset?.();
    if (!clearSearch || !hadParserSearch)
        return;
    invalidateProductSearch();
    const query = $("#drug-query");
    const status = $("#search-status");
    const results = $("#drug-results");
    if (query)
        query.value = "";
    if (status)
        status.textContent = "";
    if (results)
        results.innerHTML = "";
    updateSearchMode();
}
async function startNextParserSearch() {
    const row = state.pendingParserRows.shift();
    if (!row)
        return false;
    state.activeParserRow = row;
    state.parserRowIndex += 1;
    state.pendingParserDraft = row.draft && typeof row.draft === "object" ? { ...row.draft } : {};
    state.pendingParserPersonId = state.currentPersonId;
    state.pendingParserUncertaintyCodes = Array.isArray(row.uncertainty_codes) ? [...row.uncertainty_codes] : [];
    invalidateProductSearch();
    const query = row.product_query.trim();
    $("#drug-query").value = query;
    $("#search-status").textContent = "";
    $("#drug-results").innerHTML = "";
    updateSearchMode();
    const position = state.parserRowTotal > 1 ? ` (${state.parserRowIndex}/${state.parserRowTotal})` : "";
    const found = await runDrugSearch(`인식된 약${position} · “${query}” 제품 후보를 선택해주세요.`);
    if (found)
        $("#drug-results").scrollIntoView({ behavior: "smooth", block: "start" });
    return found;
}
function completeParserRowAndContinue() {
    if (!state.activeParserRow)
        return false;
    if (!state.pendingParserRows.length) {
        resetParserTransientState({ clearSearch: true });
        return false;
    }
    state.activeParserRow = null;
    state.pendingParserDraft = null;
    state.pendingParserPersonId = null;
    state.pendingParserUncertaintyCodes = [];
    showScreen("search", { focus: true });
    void startNextParserSearch();
    return true;
}
function focusSearchFromBox(event) {
    if (event.target?.closest?.("#drug-query-clear"))
        return;
    $("#drug-query")?.focus();
}
function showScreen(name, { focus = false } = {}) {
    const previousScreenNode = $(".screen.active");
    const previousScreen = previousScreenNode?.dataset.screen || null;
    const focusWasInPrevious = Boolean(previousScreenNode?.contains?.(document.activeElement));
    if (previousScreen === "search" && name !== "search")
        resetParserTransientState({ clearSearch: true });
    $$(".screen").forEach((node) => node.classList.toggle("active", node.dataset.screen === name));
    $$(".nav-item").forEach((node) => {
        const active = node.dataset.nav === name;
        node.classList.toggle("active", active);
        if (active)
            node.setAttribute("aria-current", "page");
        else
            node.removeAttribute("aria-current");
    });
    $("#page-title").textContent = titles[name] || "약봄";
    if (focus || focusWasInPrevious)
        focusPageTitle();
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
function medicationRegimenSummaryHtml(med) {
    const parts = [];
    if (med.dosage_text)
        parts.push(`1회 ${formatDoseText(med.dosage_text)}`);
    if (med.frequency_per_day)
        parts.push(`하루 ${med.frequency_per_day}회`);
    if (med.meal_relation && med.meal_relation !== "unspecified")
        parts.push(mealRelationLabel(med.meal_relation));
    if (med.administration_route)
        parts.push(routeLabel(med.administration_route));
    if ((med.schedules || []).length)
        parts.push(`매일 ${(med.schedules || []).map((item) => item.time_of_day).join(", ")}`);
    if (med.prescription_days && !med.course_progress)
        parts.push(`${med.prescription_days}일 복용`);
    return parts.length ? `<p class="regimen-summary">${parts.map(escapeHtml).join(" · ")}</p>` : "";
}
function currentPerson() {
    return state.people.find((person) => person.id === state.currentPersonId) || null;
}
async function loadPeople() {
    const previousPersonId = state.currentPersonId;
    state.people = await api("/api/people");
    if (!state.people.some((person) => person.id === state.currentPersonId)) {
        state.currentPersonId = state.people[0]?.id || null;
    }
    if (previousPersonId && previousPersonId !== state.currentPersonId)
        resetParserTransientState({ clearSearch: true });
    if (state.currentPersonId) {
        localStorage.setItem("medicine.currentPersonId", state.currentPersonId);
        await loadDashboard();
        recoverPersistedDoseIntents(state.currentPersonId);
    }
    else {
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
    if (!state.currentPersonId)
        return;
    const personId = state.currentPersonId;
    const existing = state.dashboardLoads.get(personId);
    if (existing) {
        existing.dirty = true;
        return existing.promise;
    }
    const entry = { dirty: false, promise: null };
    entry.promise = (async () => {
        try {
            do {
                entry.dirty = false;
                const dashboard = await api(`/api/people/${personId}/dashboard`);
                if (state.currentPersonId === personId && !entry.dirty) {
                    reconcilePrnRequestIds(dashboard?.recent_logs || []);
                    state.dashboard = dashboard;
                    state.dashboardDate = dashboard?.daily_plan?.date || todayInKorea();
                    state.dashboardStale = false;
                }
            } while (entry.dirty && state.currentPersonId === personId);
        }
        finally {
            if (state.dashboardLoads.get(personId) === entry)
                state.dashboardLoads.delete(personId);
        }
    })();
    state.dashboardLoads.set(personId, entry);
    return entry.promise;
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
        <button class="primary-button wide" id="home-add-person" type="button">프로필 추가</button>
      </div>`;
        $("#home-add-person").addEventListener("click", () => openPersonForm());
        return;
    }
    if (state.dashboardStale) {
        root.innerHTML = `
      <div class="hero-card">
        <p class="eyebrow">REFRESH REQUIRED</p>
        <h2>${escapeHtml(person.name)}님의<br>변경사항은 저장됐어요.</h2>
        <p class="muted">최신 복약 정보는 다시 확인이 필요합니다. 앱을 다시 열어 최신 상태를 확인해주세요.</p>
      </div>`;
        return;
    }
    const meds = state.dashboard?.medications || [];
    const plan = state.dashboard?.daily_plan || { doses: [], prn_medications: [], unscheduled_medications: [], summary: {} };
    const doses = plan.doses || [];
    const unscheduled = plan.unscheduled_medications || [];
    root.innerHTML = `
    <div class="hero-card">
      <p class="eyebrow">${escapeHtml(new Date().toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul", month: "long", day: "numeric", weekday: "short" }))}</p>
      <h2>${escapeHtml(person.name)}님의<br>복약을 챙겨볼게요.</h2>
      <p class="muted">현재 복용약 ${meds.length}개 · ${escapeHtml(profileMeta(person))}</p>
    </div>
    <div class="card today-card">
      <div class="today-header"><h3>오늘 복용 일정</h3><span class="count-pill">${doses.length}</span></div>
      <div class="schedule-list">
        ${doses.length
        ? doses.map(scheduleHtml).join("")
        : unscheduled.length
            ? `<div class="empty-state"><strong>시간이 정해진 복용 일정이 없어요</strong>횟수나 시간을 입력하지 않은 복용약은 아래에서 확인해주세요.</div>`
            : `<div class="empty-state"><strong>오늘 예정된 복용이 없어요</strong>처방 정보에 횟수나 시간을 입력하면 오늘 일정이 자동으로 만들어져요.</div>`}
      </div>
      ${unscheduled.length ? `<div class="prn-note"><strong>일정 정보 미입력</strong>${unscheduled.map((med) => escapeHtml(med.product_name)).join(" · ")}</div>` : ""}
      ${(plan.prn_medications || []).length ? `<div class="prn-note"><strong>필요시 복용</strong>${plan.prn_medications.map((med) => escapeHtml(med.product_name)).join(" · ")}</div>` : ""}
    </div>`;
    $$('[data-instance-taken]', root).forEach((button) => button.addEventListener("click", () => completeDoseInstance(button.dataset.instanceTaken, "taken", button)));
    $$('[data-instance-skipped]', root).forEach((button) => button.addEventListener("click", () => completeDoseInstance(button.dataset.instanceSkipped, "skipped", button)));
    $$('[data-instance-cancel]', root).forEach((button) => button.addEventListener("click", () => cancelDoseInstance(button.dataset.instanceCancel, button)));
}
function scheduleHtml(item) {
    const stateClass = item.status === "taken" ? "done taken" : item.status === "skipped" ? "done skipped" : "planned";
    const pendingLabel = item._pending ? "저장 중…" : null;
    const mealRelation = item.meal_relation && item.meal_relation !== "unspecified"
        ? mealRelationLabel(item.meal_relation) : null;
    const doseMeta = [item.dose_text ? formatDoseText(item.dose_text) : "복용량 미입력", mealRelation]
        .filter(Boolean).join(" · ");
    return `
    <div class="schedule-item ${stateClass}">
      <span class="schedule-time">${escapeHtml(item.scheduled_time || item.slot_label || "시간 미정")}</span>
      <div class="schedule-name"><strong>${escapeHtml(item.product_name)}</strong><span>${escapeHtml(doseMeta)}</span></div>
      ${item.status === "taken"
        ? `<div class="dose-result"><span class="dose-status taken">${pendingLabel || "✓ 복용 완료"}</span><button class="dose-cancel-action" data-instance-cancel="${item.id}" type="button">취소</button></div>`
        : item.status === "skipped"
            ? `<div class="dose-result"><span class="dose-status skipped">${pendingLabel || "– 건너뜀"}</span><button class="dose-cancel-action" data-instance-cancel="${item.id}" type="button">취소</button></div>`
            : `<div class="dose-actions planned">
              <button class="dose-primary-action" data-instance-taken="${item.id}" type="button">✓ 사용했어요</button>
              <button class="dose-skip-action" data-instance-skipped="${item.id}" type="button">건너뛰기</button>
              ${pendingLabel ? `<span class="dose-status">${pendingLabel}</span>` : ""}
            </div>`}
    </div>`;
}
function permitChangeCardHtml(medication) {
    if (!medication.permit_status || medication.permit_status === "active")
        return "";
    const label = permitStatusLabel(medication.permit_status, medication.permit_status_name);
    const changedAt = medication.permit_status_changed_at ? ` · ${escapeHtml(medication.permit_status_changed_at)}` : "";
    return `<div class="permit-change-note">
    <div><span class="permit-change-badge">허가상태 변경</span><strong>${escapeHtml(label)}${changedAt}</strong></div>
    <p>현재 품목 허가상태가 변경됐어요. 이 상태만으로 복용을 중단하지 말고 의사·약사와 확인하세요.</p>
  </div>`;
}
function renderMedications() {
    const medsRoot = $("#medications-list");
    const historyRoot = $("#dose-history");
    if (state.dashboardStale) {
        medsRoot.innerHTML = `<div class="empty-state"><strong>변경사항은 저장됐어요</strong>최신 복약 정보는 다시 확인이 필요합니다. 앱을 다시 열어 최신 상태를 확인해주세요.</div>`;
        historyRoot.innerHTML = "";
        return;
    }
    const meds = state.dashboard?.medications || [];
    const logs = state.dashboard?.recent_logs || [];
    medsRoot.innerHTML = meds.length ? meds.map((med) => `
    <article class="card med-card">
      <div class="med-row">
        <div><p class="eyebrow">${escapeHtml(med.ingredient_name || "MEDICINE")}</p><h3>${escapeHtml(med.product_name)}</h3></div>
        <div class="med-badges">
          ${med.split_prohibited ? `<button class="split-caution-badge" data-dur-alert="${med.id}" type="button" aria-label="분할불가 주의사항 보기" title="분할불가 주의사항 보기">분할불가</button>` : ""}
          ${med.dur_alert ? `<button class="dur-alert-badge" data-dur-alert="${med.id}" type="button" aria-label="현재 DUR 주의 항목 보기" title="현재 DUR 주의 항목 보기">DUR 주의</button>` : med.dur_review_required ? `<button class="dur-review-badge" data-dur-alert="${med.id}" type="button" aria-label="확인이 필요한 DUR 항목 보기" title="확인이 필요한 DUR 항목 보기">DUR 확인 필요</button>` : ""}
        </div>
      </div>
      ${medicationCourseHtml(med.course_progress)}
      ${medicationRegimenSummaryHtml(med)}
      ${permitChangeCardHtml(med)}
      <div class="med-meta">
        ${med.as_needed ? `<span class="chip prn-chip">필요할 때 복용</span>` : ""}
        ${med.prn_max_per_day ? `<span class="chip">1일 최대 ${escapeHtml(med.prn_max_per_day)}회</span>` : ""}
        ${med.long_term ? `<span class="chip">장기복용 · 종료일 없음</span>` : ""}
        ${med.source === "manual" ? `<span class="chip caution-chip">직접 입력 · DUR 제한</span>` : ""}
      </div>
      <div class="med-actions">
        ${med.as_needed ? `<button class="primary-button wide" data-prn-taken="${med.id}" type="button">지금 복용 기록</button>` : ""}
        <div class="med-secondary-actions">
          <button class="secondary-button" data-edit="${med.id}" type="button">처방 수정</button>
          <button class="stop-button" data-stop="${med.id}" type="button">복용 종료</button>
        </div>
      </div>
    </article>`).join("") : `<div class="empty-state"><strong>복용 중인 약이 없어요</strong>약 검색 탭에서 추가해보세요.</div>`;
    historyRoot.innerHTML = logs.length ? logs.map((log) => `
    <div class="history-row">
      <span class="history-icon">${log.status === "taken" ? "✓" : "–"}</span>
      <div><strong>${escapeHtml(log.product_name)}</strong><div class="history-time">${escapeHtml(log.status === "taken" ? "사용 완료" : "건너뜀")}</div></div>
      <div class="history-controls"><span class="history-time">${escapeHtml(formatTime(log.occurred_at))}</span>${log.dose_instance_id ? `<button class="mini-action" data-log-cancel="${escapeHtml(log.dose_instance_id)}" type="button">되돌리기</button>` : ""}</div>
    </div>`).join("") : `<div class="empty-state"><strong>기록이 아직 없어요</strong>약을 복용한 뒤 완료 버튼을 눌러보세요.</div>`;
    $$('[data-dur-alert]', medsRoot).forEach((button) => button.addEventListener("click", () => openMedicationSafety(button.dataset.durAlert)));
    $$('[data-prn-taken]', medsRoot).forEach((button) => button.addEventListener("click", () => recordPrnIntake(button.dataset.prnTaken, button)));
    $$('[data-edit]', medsRoot).forEach((button) => button.addEventListener("click", () => openMedicationEdit(button.dataset.edit)));
    $$('[data-stop]', medsRoot).forEach((button) => button.addEventListener("click", () => stopMedication(button.dataset.stop)));
    $$('[data-log-cancel]', historyRoot).forEach((button) => button.addEventListener("click", () => cancelDoseInstance(button.dataset.logCancel, button)));
}
function stopMedication(medicationId) {
    const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
    if (!medication)
        return;
    state.pendingStopMedicationId = medicationId;
    $("#stop-medication-copy").textContent = `${medication.product_name} 복용을 종료합니다. 기존 복용 기록은 그대로 남습니다.`;
    openSheet("#stop-medication-sheet");
}
async function confirmStopMedication() {
    const medicationId = state.pendingStopMedicationId;
    if (!medicationId)
        return;
    const medication = (state.dashboard?.medications || []).find((item) => item.id === medicationId);
    const query = medication ? `?expected_revision=${medication.revision}` : "";
    let stopped;
    try {
        stopped = await api(`/api/medications/${medicationId}${query}`, { method: "DELETE" });
    }
    catch (error) {
        toast(error.message);
        return;
    }
    reconcileCommittedMedication(stopped);
    markDashboardStale();
    state.pendingStopMedicationId = null;
    closeSheetsAfterMutation();
    renderAll();
    showScreen("meds", { focus: true });
    try {
        await loadDashboard();
        renderAll();
        toast("복용을 종료했어요");
    }
    catch (error) {
        console.error("dashboard refresh after medication stop failed", error);
        markDashboardStale();
        renderAll();
        toast("복용은 종료됐지만 목록을 새로고침하지 못했어요. 앱을 다시 열면 최신 상태를 확인할 수 있어요.");
    }
}
function formatTime(value) {
    try {
        return new Date(value).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    }
    catch (_) {
        return value;
    }
}
function updateSearchMode() {
    const hero = $(".search-hero");
    if (!hero)
        return;
    hero.classList.toggle("has-query", Boolean($("#drug-query").value.trim()));
}
async function runDrugSearch(successMessage = "") {
    const term = $("#drug-query").value.trim();
    const status = $("#search-status");
    const root = $("#drug-results");
    state.searchTimer = null;
    updateSearchMode();
    if (!term) {
        invalidateProductSearch();
        status.textContent = "";
        root.innerHTML = "";
        return false;
    }
    const requestId = ++state.searchRequestId;
    state.searchObserver?.disconnect?.();
    state.searchObserver = null;
    state.searchTerm = term;
    state.searchNextOffset = null;
    state.searchHasMore = false;
    state.searchLoadingMore = false;
    status.textContent = "";
    try {
        const page = assertProductSearchPage(await api(`/api/products?q=${encodeURIComponent(term)}&limit=30&offset=0`, {
            coalesceKey: "product-search",
        }));
        if (requestId !== state.searchRequestId || $("#drug-query").value.trim() !== term)
            return false;
        state.fullCatalog = true;
        status.textContent = successMessage;
        renderProductSearchPage(page);
        state.searchHasMore = page.has_more;
        state.searchNextOffset = page.next_offset;
        observeProductSearchMore(term, requestId);
        return true;
    }
    catch (error) {
        if (requestId !== state.searchRequestId || $("#drug-query").value.trim() !== term)
            return false;
        status.textContent = friendlyErrorMessage(error.message);
        root.innerHTML = "";
        return false;
    }
}
function selectProductResult(card) {
    let parserDraft = null;
    let uncertaintyCodes = [];
    if (state.pendingParserDraft) {
        if (state.pendingParserPersonId !== state.currentPersonId) {
            resetParserTransientState({ clearSearch: true });
        }
        else {
            parserDraft = { ...state.pendingParserDraft };
            uncertaintyCodes = [...state.pendingParserUncertaintyCodes];
        }
    }
    previewProduct(card.dataset.productSelect, parserDraft, uncertaintyCodes);
}
async function refreshForDateChange() {
    if (!state.currentPersonId || document.visibilityState === "hidden")
        return;
    if (state.dashboardDate === todayInKorea())
        return;
    try {
        await loadDashboard();
        renderAll();
    }
    catch (error) {
        console.error("date rollover refresh failed", error);
    }
}
function bindEvents() {
    $$("[data-nav]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.nav)));
    $$("[data-go]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.go)));
    bindPeopleEvents();
    $("#sheet-backdrop").addEventListener("click", closeSheets);
    $$('[data-close-sheet]').forEach((button) => button.addEventListener("click", closeSheets));
    $("#confirm-stop-medication").addEventListener("click", confirmStopMedication);
    $(".search-box").addEventListener("click", focusSearchFromBox);
    $("#drug-query-clear").addEventListener("click", () => {
        const query = $("#drug-query");
        if (!query.value)
            return;
        query.value = "";
        query.dispatchEvent(new Event("input", { bubbles: true }));
    });
    $("#drug-query").addEventListener("input", () => {
        invalidateProductSearch();
        // Keep any prefilled medication details while the user edits only the product query.
        $("#search-status").textContent = "";
        $("#drug-results").innerHTML = "";
        updateSearchMode();
        state.searchTimer = setTimeout(runDrugSearch, 280);
    });
    $("#drug-results").addEventListener("click", (event) => {
        const card = event.target?.closest?.("[data-product-select]");
        if (card)
            selectProductResult(card);
    });
    $("#drug-results").addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ")
            return;
        const card = event.target?.closest?.("[data-product-select]");
        if (!card)
            return;
        event.preventDefault();
        selectProductResult(card);
    });
    document.addEventListener("medicine:sheet-closed", (event) => {
        const detail = event.detail;
        if (detail?.id === "stop-medication-sheet")
            state.pendingStopMedicationId = null;
    });
    window.addEventListener("medicine:parser-result", (event) => {
        const detail = event.detail;
        const rows = Array.isArray(detail?.rows) ? detail.rows : [];
        if (!rows.length)
            return;
        resetParserTransientState({ clearSearch: true });
        state.pendingParserRows = rows.map((row) => ({
            ...row,
            draft: row.draft && typeof row.draft === "object" ? { ...row.draft } : {},
            uncertainty_codes: Array.isArray(row.uncertainty_codes) ? [...row.uncertainty_codes] : [],
        }));
        state.parserRowTotal = state.pendingParserRows.length;
        showScreen("search", { focus: true });
        void startNextParserSearch();
    });
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible")
        refreshForDateChange(); });
    window.addEventListener("focus", refreshForDateChange);
    setInterval(refreshForDateChange, 60000);
}
document.addEventListener("DOMContentLoaded", async () => {
    bindEvents();
    try {
        await loadHealth();
        await loadPeople();
        const requestedScreen = new URLSearchParams(window.location.search).get("screen");
        if (requestedScreen && Object.hasOwn(titles, requestedScreen))
            showScreen(requestedScreen);
    }
    catch (error) {
        console.error("app initialization failed", error);
        toast("앱을 불러오지 못했어요. 다시 열어주세요.");
    }
});
