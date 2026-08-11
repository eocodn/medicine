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
  return parts.join(" · ");
}

function renderProfileShortcut() {
  const person = currentPerson();
  $("#profile-shortcut").textContent = person ? person.name.slice(0, 1) : "+";
}

function renderPeople() {
  const root = $("#people-list");
  root.innerHTML = state.people.length ? state.people.map((person) => `
    <article class="card person-card">
      <button class="person-select" data-person-select="${person.id}" type="button">
        <div class="person-row">
          <span class="person-avatar">${escapeHtml(person.name.slice(0, 1))}</span>
          <div class="person-copy"><h3>${escapeHtml(person.name)}</h3><p>${escapeHtml(profileMeta(person))}</p></div>
          ${person.id === state.currentPersonId ? `<span class="selected-badge">관리 중</span>` : ""}
        </div>
      </button>
      <div class="person-actions">
        <button class="secondary-button" data-person-edit="${person.id}" type="button">정보 수정</button>
        <button class="danger-ghost" data-person-delete="${person.id}" type="button">삭제</button>
      </div>
    </article>`).join("") : `<div class="empty-state"><strong>프로필이 없어요</strong>여러 사람의 복약을 한 기기에서 따로 관리할 수 있어요.</div>`;
  $$('[data-person-select]', root).forEach((button) => button.addEventListener("click", () => selectPerson(button.dataset.personSelect)));
  $$('[data-person-edit]', root).forEach((button) => button.addEventListener("click", () => openPersonForm(button.dataset.personEdit)));
  $$('[data-person-delete]', root).forEach((button) => button.addEventListener("click", () => deletePerson(button.dataset.personDelete)));
}

async function selectPerson(personId) {
  state.currentPersonId = personId;
  localStorage.setItem("medicine.currentPersonId", personId);
  await loadDashboard();
  renderAll();
  showScreen("home");
  toast(`${currentPerson().name}님으로 전환했어요`);
}

function syncReproductiveFields() {
  const form = $("#person-form");
  const sex = form.elements.sex.value;
  const pregnancy = form.elements.pregnancy_status;
  const lactation = form.elements.lactation_status;
  const hidden = sex === "male";
  $$('[data-reproductive-field]', form).forEach((node) => node.classList.toggle("hidden", hidden));
  if (hidden) {
    pregnancy.value = "not_applicable";
    lactation.value = "not_applicable";
    pregnancy.dataset.autoNotApplicable = "1";
    lactation.dataset.autoNotApplicable = "1";
  } else {
    for (const input of [pregnancy, lactation]) {
      if (input.dataset.autoNotApplicable === "1") input.value = "unknown";
      delete input.dataset.autoNotApplicable;
    }
  }
}

function openPersonForm(personId = null) {
  const form = $("#person-form");
  const person = state.people.find((item) => item.id === personId) || null;
  state.editingPersonId = person?.id || null;
  form.reset();
  $("#person-form-title").textContent = person ? "관리 대상 정보 수정" : "관리 대상 추가";
  $("#person-submit").textContent = person ? "변경 내용 저장" : "프로필 저장";
  if (person) {
    form.elements.name.value = person.name;
    form.elements.birth_date.value = person.birth_date;
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
  const payload = Object.fromEntries(new FormData(formElement).entries());
  const editingId = state.editingPersonId;
  try {
    const person = await api(editingId ? `/api/people/${editingId}` : "/api/people", {
      method: editingId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    if (!editingId) {
      state.currentPersonId = person.id;
      localStorage.setItem("medicine.currentPersonId", person.id);
    }
    state.editingPersonId = null;
    formElement.reset();
    closeSheets();
    await loadPeople();
    showScreen(editingId ? "people" : "home");
    toast(editingId ? `${person.name}님 정보를 수정했어요` : `${person.name}님 프로필을 만들었어요`);
  } catch (error) { toast(error.message); }
}

async function deletePerson(personId) {
  const person = state.people.find((item) => item.id === personId);
  if (!person) return;
  if (!confirm(`${person.name}님의 복용약, 복용 기록, 일정과 변경 이력을 모두 삭제할까요? 이 작업은 되돌릴 수 없어요.`)) return;
  try {
    await api(`/api/people/${personId}`, { method: "DELETE" });
    if (state.currentPersonId === personId) {
      state.currentPersonId = null;
      localStorage.removeItem("medicine.currentPersonId");
    }
    await loadPeople();
    showScreen(state.people.length ? "people" : "home");
    toast(`${person.name}님의 관리 데이터를 삭제했어요`);
  } catch (error) { toast(error.message); }
}

function bindPeopleEvents() {
  const form = $("#person-form");
  const birthInput = form.elements.birth_date;
  birthInput.max = todayInKorea();
  $("#profile-shortcut").addEventListener("click", () => state.people.length ? showScreen("people") : openPersonForm());
  $("#open-person-form").addEventListener("click", () => openPersonForm());
  form.addEventListener("submit", submitPerson);
  form.elements.sex.addEventListener("change", syncReproductiveFields);
}
