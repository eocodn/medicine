function sexLabel(value) {
  return { female: "여성", male: "남성", other: "기타", unknown: "성별 미입력" }[value] || value;
}

function pregnancyLabel(value) {
  return {
    pregnant: "임신 중",
    not_pregnant: "임신 중 아님",
    not_applicable: "해당 없음",
    unknown: "임신 여부 미입력",
  }[value] || value;
}

function lactationLabel(value) {
  return {
    breastfeeding: "수유 중",
    not_breastfeeding: "수유 중 아님",
    not_applicable: "해당 없음",
    unknown: "수유 여부 미입력",
  }[value] || value;
}

function profileMeta(person) {
  const parts = [`만 ${person.age}세`, sexLabel(person.sex)];
  if (person.sex !== "male") {
    if (person.pregnancy_status === "pregnant") parts.push(pregnancyLabel(person.pregnancy_status));
    if (person.lactation_status === "breastfeeding") parts.push(lactationLabel(person.lactation_status));
  }
  if (person.profile_needs_review) parts.push("정보 확인 필요");
  return parts.join(" · ");
}

function renderProfileShortcut() {
  const person = currentPerson();
  $("#profile-shortcut-avatar").textContent = person ? person.name.slice(0, 1) : "+";
  $("#profile-shortcut-name").textContent = person ? person.name : "선택";
  $("#profile-shortcut").setAttribute("aria-label", person ? `프로필 변경: ${person.name}` : "프로필 선택");
}

function renderPeople() {
  const root = $("#people-list");
  root.innerHTML = state.people.length ? state.people.map((person) => `
    <article class="card person-card">
      <div class="person-select" role="button" tabindex="0" data-person-select="${person.id}">
        <div class="person-row">
          <span class="person-avatar">${escapeHtml(person.name.slice(0, 1))}</span>
          <div class="person-copy"><h3>${escapeHtml(person.name)}</h3><p>${escapeHtml(profileMeta(person))}</p></div>
          ${person.id === state.currentPersonId ? `<span class="selected-badge">선택됨</span>` : ""}
        </div>
      </div>
      <div class="person-actions">
        <button class="secondary-button" data-person-edit="${person.id}" type="button">정보 수정</button>
        <button class="danger-ghost" data-person-delete="${person.id}" type="button">삭제</button>
      </div>
    </article>`).join("") : `<div class="empty-state"><strong>프로필이 없어요</strong>여러 사람의 복약을 한 기기에서 따로 관리할 수 있어요.</div>`;
  $$('[data-person-select]', root).forEach((target) => {
    target.addEventListener("click", () => selectPerson(target.dataset.personSelect));
    target.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      selectPerson(target.dataset.personSelect);
    });
  });
  $$('[data-person-edit]', root).forEach((button) => button.addEventListener("click", () => openPersonForm(button.dataset.personEdit)));
  $$('[data-person-delete]', root).forEach((button) => button.addEventListener("click", () => deletePerson(button.dataset.personDelete)));
}

async function selectPerson(personId) {
  // MEDICINE_OCR_START
  if (personId !== state.currentPersonId && typeof resetParserTransientState === "function") {
    resetParserTransientState({ clearSearch: true });
  }
  // MEDICINE_OCR_END
  state.currentPersonId = personId;
  localStorage.setItem("medicine.currentPersonId", personId);
  await loadDashboard();
  recoverPersistedDoseIntents(personId);
  renderAll();
  showScreen("home", { focus: true });
}

function syncReproductiveFields() {
  const form = $("#person-form");
  const female = form.elements.sex.value === "female";
  for (const input of [form.elements.pregnancy_status, form.elements.lactation_status]) {
    input.disabled = !female;
  }
  $$('[data-reproductive-field]', form).forEach((node) => node.classList.toggle("hidden", !female));
}

function populateBirthDateOptions() {
  const form = $("#person-form");
  const currentYear = Number(todayInKorea().slice(0, 4));
  form.elements.birth_year.max = String(currentYear);
  form.elements.birth_month.innerHTML = [
    `<option value="" selected disabled>월</option>`,
    ...Array.from({ length: 12 }, (_, index) => index + 1)
      .map((month) => `<option value="${String(month).padStart(2, "0")}">${month}월</option>`),
  ].join("");
  updateBirthDayOptions();
}

function updateBirthDayOptions(preferredDay = "") {
  const form = $("#person-form");
  const yearText = form.elements.birth_year.value;
  const year = /^\d{4}$/.test(yearText) ? Number(yearText) : 0;
  const month = Number(form.elements.birth_month.value);
  const maxDay = year && month ? new Date(year, month, 0).getDate() : 31;
  const previousDay = preferredDay || form.elements.birth_day.value;
  form.elements.birth_day.innerHTML = [
    `<option value="" selected disabled>일</option>`,
    ...Array.from({ length: maxDay }, (_, index) => index + 1)
      .map((day) => `<option value="${String(day).padStart(2, "0")}">${day}일</option>`),
  ].join("");
  if (previousDay && Number(previousDay) <= maxDay) form.elements.birth_day.value = previousDay;
}

function syncBirthDateFields() {
  const form = $("#person-form");
  const year = form.elements.birth_year.value;
  const month = form.elements.birth_month.value;
  const day = form.elements.birth_day.value;
  const currentYear = Number(todayInKorea().slice(0, 4));
  const validYear = /^\d{4}$/.test(year) && Number(year) >= 1000 && Number(year) <= currentYear;
  form.elements.birth_date.value = validYear && month && day ? `${year}-${month}-${day}` : "";
}

function setBirthDateFields(value = null) {
  const form = $("#person-form");
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  form.elements.birth_year.value = match?.[1] || "";
  form.elements.birth_month.value = match?.[2] || "";
  updateBirthDayOptions(match?.[3] || "");
  form.elements.birth_day.value = match?.[3] || "";
  syncBirthDateFields();
}


function openPersonForm(personId = null) {
  const form = $("#person-form");
  const person = state.people.find((item) => item.id === personId) || null;
  state.editingPersonId = person?.id || null;
  form.reset();
  setBirthDateFields(person?.birth_date || null);
  $("#person-form-title").textContent = person ? "프로필 정보 수정" : "프로필 추가";
  $("#person-submit").textContent = person ? "변경 내용 저장" : "프로필 저장";
  if (person) {
    form.elements.name.value = person.name;
    form.elements.sex.value = person.sex;
    form.elements.pregnancy_status.value = person.pregnancy_status;
    form.elements.lactation_status.value = person.lactation_status || "unknown";
    form.elements.notes.value = person.notes || "";
  }
  syncReproductiveFields();
  openSheet("#person-sheet");
}

async function submitPerson(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  syncBirthDateFields();
  const payload = Object.fromEntries(new FormData(formElement).entries());
  delete payload.birth_year;
  delete payload.birth_month;
  delete payload.birth_day;
  if (!payload.birth_date || payload.birth_date > todayInKorea()) {
    toast("생년월일은 오늘 또는 이전 날짜로 선택해주세요.");
    return;
  }
  if (payload.sex === "male") {
    payload.pregnancy_status = "not_applicable";
    payload.lactation_status = "not_applicable";
  }
  const editingId = state.editingPersonId;
  let person;
  try {
    person = await api(editingId ? `/api/people/${editingId}` : "/api/people", {
      method: editingId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
  } catch (error) {
    toast(error.message);
    return;
  }

  const targetScreen = editingId ? "people" : "home";
  const dashboardAffected = reconcileCommittedPerson(person, { select: !editingId });
  if (dashboardAffected) markDashboardStale();
  state.editingPersonId = null;
  formElement.reset();
  closeSheetsAfterMutation();
  renderAll();
  showScreen(targetScreen, { focus: true });
  try {
    await loadPeople();
    showScreen(targetScreen, { focus: true });
  } catch (error) {
    console.error("people refresh after profile save failed", error);
    if (dashboardAffected) markDashboardStale();
    renderAll();
    showScreen(targetScreen, { focus: true });
    toast("프로필은 저장됐지만 화면을 새로고침하지 못했어요. 앱을 다시 열면 저장된 프로필을 확인할 수 있어요.");
  }
}

async function deletePerson(personId) {
  const person = state.people.find((item) => item.id === personId);
  if (!person) return;
  state.pendingDeletePersonId = personId;
  $("#delete-person-copy").textContent = `${person.name}님의 복용약, 복용 기록과 일정을 모두 삭제합니다. 이 작업은 되돌릴 수 없어요.`;
  openSheet("#delete-person-sheet");
}

async function confirmDeletePerson() {
  const personId = state.pendingDeletePersonId;
  if (!personId) return;
  try {
    await api(`/api/people/${personId}`, { method: "DELETE" });
  } catch (error) {
    toast(error.message);
    return;
  }

  const dashboardAffected = reconcileDeletedPerson(personId);
  if (dashboardAffected) markDashboardStale();
  const targetScreen = state.people.length ? "people" : "home";
  state.pendingDeletePersonId = null;
  closeSheetsAfterMutation();
  renderAll();
  showScreen(targetScreen, { focus: true });
  try {
    await loadPeople();
    showScreen(state.people.length ? "people" : "home", { focus: true });
  } catch (error) {
    console.error("people refresh after profile delete failed", error);
    if (dashboardAffected) markDashboardStale();
    renderAll();
    showScreen(targetScreen, { focus: true });
    toast("프로필은 삭제됐지만 화면을 새로고침하지 못했어요. 앱을 다시 열면 최신 상태를 확인할 수 있어요.");
  }
}

function bindPeopleEvents() {
  const form = $("#person-form");
  populateBirthDateOptions();
  $("#profile-shortcut").addEventListener("click", () => state.people.length ? showScreen("people") : openPersonForm());
  $("#open-person-form").addEventListener("click", () => openPersonForm());
  $("#confirm-delete-person").addEventListener("click", confirmDeletePerson);
  form.addEventListener("submit", submitPerson);
  form.elements.birth_year.addEventListener("input", () => { updateBirthDayOptions(); syncBirthDateFields(); });
  form.elements.birth_month.addEventListener("change", () => { updateBirthDayOptions(); syncBirthDateFields(); });
  form.elements.birth_day.addEventListener("change", syncBirthDateFields);
  form.elements.sex.addEventListener("change", syncReproductiveFields);
  document.addEventListener("medicine:sheet-closed", (event) => {
    const detail = (event as CustomEvent).detail;
    if (detail?.id === "person-sheet") {
      state.editingPersonId = null;
      form.reset();
      setBirthDateFields(null);
      syncReproductiveFields();
    }
    if (detail?.id === "delete-person-sheet") state.pendingDeletePersonId = null;
  });
}
