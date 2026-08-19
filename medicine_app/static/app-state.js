const state = {
  people: [],
  currentPersonId: localStorage.getItem("medicine.currentPersonId"),
  dashboard: null,
  dashboardDate: null,
  dashboardStale: false,
  fullCatalog: false,
  pendingProduct: null,
  pendingRequestId: null,
  pendingOcrDraft: null,
  pendingOcrPersonId: null,
  ocrSearchActive: false,
  warningToken: null,
  reviewedDraftKey: null,
  editingMedicationId: null,
  editingPersonId: null,
  pendingDeletePersonId: null,
  pendingStopMedicationId: null,
  searchTimer: null,
  searchRequestId: 0,
};

function markDashboardStale() {
  state.dashboardStale = true;
  state.dashboardDate = null;
}

function reconcileCommittedMedication(committed) {
  if (!committed?.id) return false;
  const dashboard = state.dashboard || {};
  const medications = dashboard.medications || [];
  const existing = medications.find((item) => item.id === committed.id) || {};
  let next;
  if (committed.active === false) {
    next = medications.filter((item) => item.id !== committed.id);
  } else {
    const merged = { ...existing, ...committed, course_progress: null };
    next = medications.some((item) => item.id === committed.id)
      ? medications.map((item) => item.id === committed.id ? merged : item)
      : [...medications, merged];
  }
  state.dashboard = { ...dashboard, medications: next };
  state.dashboardDate = null;
  return true;
}

function reconcileCommittedPerson(person, { select = false } = {}) {
  if (!person?.id) return false;
  const affectsCurrentDashboard = select || person.id === state.currentPersonId;
  const exists = state.people.some((item) => item.id === person.id);
  state.people = exists
    ? state.people.map((item) => item.id === person.id ? person : item)
    : [...state.people, person];
  if (select) {
    if (state.currentPersonId && state.currentPersonId !== person.id && typeof resetOcrTransientState === "function") {
      resetOcrTransientState({ clearSearch: true });
    }
    state.currentPersonId = person.id;
    localStorage.setItem("medicine.currentPersonId", person.id);
    state.dashboard = null;
    state.dashboardDate = null;
  }
  return affectsCurrentDashboard;
}

function reconcileDeletedPerson(personId) {
  const deletingCurrent = state.currentPersonId === personId;
  state.people = state.people.filter((person) => person.id !== personId);
  if (!deletingCurrent) return false;
  if (typeof resetOcrTransientState === "function") resetOcrTransientState({ clearSearch: true });
  state.currentPersonId = state.people[0]?.id || null;
  state.dashboard = null;
  state.dashboardDate = null;
  if (state.currentPersonId) localStorage.setItem("medicine.currentPersonId", state.currentPersonId);
  else {
    localStorage.removeItem("medicine.currentPersonId");
    state.dashboardStale = false;
  }
  return Boolean(state.currentPersonId);
}
