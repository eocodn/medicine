type DashboardPhase = "empty" | "loading" | "ready" | "stale" | "error";

type DashboardSession = {
  ownerPersonId: string | null;
  date: string | null;
  phase: DashboardPhase;
  data: any;
  generation: number;
  reason: string | null;
};

function initialDashboardSession(): DashboardSession {
  return {
    ownerPersonId: null,
    date: null,
    phase: "empty",
    data: null,
    generation: 0,
    reason: null,
  };
}

const state = {
  people: [],
  currentPersonId: localStorage.getItem("medicine.currentPersonId"),
  dashboardSession: initialDashboardSession(),
  referenceAvailable: true,
  pendingProduct: null,
  pendingRequestId: null,
  pendingOcrProductRows: [],
  activeOcrProductRow: null,
  ocrProductRowTotal: 0,
  ocrProductRowIndex: 0,
  warningToken: null,
  reviewedDraftKey: null,
  editingMedicationId: null,
  editingPersonId: null,
  pendingDeletePersonId: null,
  pendingStopMedicationId: null,
  searchTimer: null,
  searchRequestId: 0,
  searchTerm: null,
  searchNextOffset: null,
  searchHasMore: false,
  searchLoadingMore: false,
  searchObserver: null,
  doseMutations: new Map(),
  prnRequests: new Map(),
  dashboardLoads: new Map(),
};

const DOSE_INTENTS_STORAGE_KEY = "medicine.doseIntents";

type PersistedDoseIntent = {
  personId: string;
  desiredStatus: string;
};

function dashboardData() {
  return state.dashboardSession.data;
}

function dashboardBelongsToCurrentPerson() {
  return Boolean(
    state.currentPersonId &&
    state.dashboardSession.ownerPersonId === state.currentPersonId,
  );
}

function dashboardReadyForCurrent(date = todayInKorea()) {
  const session = state.dashboardSession;
  const dashboard = session.data;
  return dashboardBelongsToCurrentPerson()
    && session.phase === "ready"
    && session.date === date
    && Boolean(dashboard)
    && (!dashboard?.person?.id || dashboard.person.id === state.currentPersonId)
    && (!dashboard?.daily_plan?.date || dashboard.daily_plan.date === date);
}

function clearDashboardSession() {
  state.dashboardSession = {
    ...initialDashboardSession(),
    generation: state.dashboardSession.generation + 1,
  };
}

function activateDashboardOwner(personId) {
  state.dashboardSession = {
    ownerPersonId: personId || null,
    date: null,
    phase: "empty",
    data: null,
    generation: state.dashboardSession.generation + 1,
    reason: null,
  };
}

function beginDashboardLoad(personId, date) {
  const current = state.dashboardSession;
  const preserve = current.ownerPersonId === personId && current.date === date
    ? current.data
    : null;
  const generation = current.generation + 1;
  state.dashboardSession = {
    ownerPersonId: personId,
    date,
    phase: "loading",
    data: preserve,
    generation,
    reason: null,
  };
  return generation;
}

function commitDashboardLoad(personId, date, generation, dashboard) {
  if (dashboard?.person?.id && dashboard.person.id !== personId) {
    throw new Error("dashboard owner mismatch");
  }
  if (dashboard?.daily_plan?.date && dashboard.daily_plan.date !== date) {
    throw new Error("dashboard date mismatch");
  }
  const current = state.dashboardSession;
  if (
    state.currentPersonId !== personId ||
    current.ownerPersonId !== personId ||
    current.date !== date ||
    current.generation !== generation
  ) return false;
  reconcilePrnRequestIds(dashboard?.recent_logs || []);
  state.dashboardSession = {
    ownerPersonId: personId,
    date,
    phase: "ready",
    data: dashboard,
    generation,
    reason: null,
  };
  return true;
}

function failDashboardLoad(personId, date, generation, error) {
  const current = state.dashboardSession;
  if (
    state.currentPersonId !== personId ||
    current.ownerPersonId !== personId ||
    current.date !== date ||
    current.generation !== generation
  ) return false;
  state.dashboardSession = {
    ...current,
    phase: "error",
    reason: String(error?.message || "dashboard load failed"),
  };
  return true;
}

function markDashboardStale(reason = "refresh_required") {
  const personId = state.currentPersonId;
  if (!personId) {
    clearDashboardSession();
    return;
  }
  const current = state.dashboardSession;
  const sameOwner = current.ownerPersonId === personId;
  state.dashboardSession = {
    ownerPersonId: personId,
    date: sameOwner ? current.date : null,
    phase: "stale",
    data: sameOwner ? current.data : null,
    generation: current.generation + 1,
    reason,
  };
}

function selectCurrentPerson(personId) {
  const nextPersonId = personId || null;
  const changed = state.currentPersonId !== nextPersonId;
  if (changed && state.currentPersonId && typeof resetOcrProductDiscovery === "function") {
    resetOcrProductDiscovery({ clearSearch: true });
  }
  state.currentPersonId = nextPersonId;
  if (nextPersonId) localStorage.setItem("medicine.currentPersonId", nextPersonId);
  else localStorage.removeItem("medicine.currentPersonId");
  if (changed || state.dashboardSession.ownerPersonId !== nextPersonId) {
    if (nextPersonId) activateDashboardOwner(nextPersonId);
    else clearDashboardSession();
  }
  return changed;
}

function persistedDoseIntents(): Record<string, PersistedDoseIntent> {
  const raw = localStorage.getItem(DOSE_INTENTS_STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid dose intents");
    return parsed as Record<string, PersistedDoseIntent>;
  } catch (_) {
    localStorage.removeItem(DOSE_INTENTS_STORAGE_KEY);
    return {};
  }
}

function writePersistedDoseIntents(intents) {
  if (Object.keys(intents).length) {
    localStorage.setItem(DOSE_INTENTS_STORAGE_KEY, JSON.stringify(intents));
  } else {
    localStorage.removeItem(DOSE_INTENTS_STORAGE_KEY);
  }
}

function rememberScheduledDoseIntent(instanceId, desiredStatus) {
  const scheduled = (dashboardData()?.daily_plan?.doses || []).some((item) => item.id === instanceId);
  if (!scheduled || !state.currentPersonId || !dashboardBelongsToCurrentPerson()) return false;
  const intents = persistedDoseIntents();
  intents[instanceId] = { personId: state.currentPersonId, desiredStatus };
  writePersistedDoseIntents(intents);
  return true;
}

function clearScheduledDoseIntent(instanceId) {
  const intents = persistedDoseIntents();
  if (!Object.hasOwn(intents, instanceId)) return false;
  delete intents[instanceId];
  writePersistedDoseIntents(intents);
  return true;
}

function prnRequestStorageKey(medicationId) {
  return `medicine.prnRequest.${medicationId}`;
}

function pendingPrnRequestId(medicationId) {
  let requestId = state.prnRequests.get(medicationId);
  if (!requestId) {
    requestId = localStorage.getItem(prnRequestStorageKey(medicationId));
    if (requestId) state.prnRequests.set(medicationId, requestId);
  }
  return requestId;
}

function rememberPrnRequestId(medicationId, requestId) {
  state.prnRequests.set(medicationId, requestId);
  localStorage.setItem(prnRequestStorageKey(medicationId), requestId);
}

function clearPrnRequestId(medicationId) {
  state.prnRequests.delete(medicationId);
  localStorage.removeItem(prnRequestStorageKey(medicationId));
}

function reconcilePrnRequestIds(logs) {
  for (const log of logs || []) {
    if (!log?.medication_id || !log?.request_id) continue;
    if (pendingPrnRequestId(log.medication_id) === log.request_id) {
      clearPrnRequestId(log.medication_id);
    }
  }
}

function recomputeDoseSummary() {
  const plan = dashboardData()?.daily_plan;
  if (!plan) return;
  const doses = plan.doses || [];
  plan.summary = {
    planned: doses.filter((item) => item.status === "planned").length,
    taken: doses.filter((item) => item.status === "taken").length,
    skipped: doses.filter((item) => item.status === "skipped").length,
  };
}

function currentDoseStatus(instanceId) {
  const dashboard = dashboardData();
  const localDose = (dashboard?.daily_plan?.doses || []).find((item) => item.id === instanceId);
  if (localDose) return localDose.status;
  const log = (dashboard?.recent_logs || []).find((item) => item.dose_instance_id === instanceId);
  return log?.status || null;
}

function applyPendingDoseIntent(instanceId, desiredStatus) {
  if (!dashboardBelongsToCurrentPerson()) return false;
  const localDose = (dashboardData()?.daily_plan?.doses || []).find((item) => item.id === instanceId);
  if (!localDose) return false;
  localDose.status = desiredStatus;
  if (desiredStatus === "planned") localDose.completed_at = null;
  localDose._pending = true;
  recomputeDoseSummary();
  return true;
}

function clearPendingDoseIntent(instanceId) {
  const localDose = (dashboardData()?.daily_plan?.doses || []).find((item) => item.id === instanceId);
  if (!localDose) return false;
  delete localDose._pending;
  return true;
}

function reconcileDoseMutation(committed) {
  const dashboard = dashboardData();
  if (!committed || !dashboard || !dashboardBelongsToCurrentPerson()) return false;
  let changed = false;
  if (Array.isArray(committed.recent_logs)) {
    reconcilePrnRequestIds(committed.recent_logs);
    state.dashboardSession.data = {
      ...dashboard,
      recent_logs: committed.recent_logs,
    };
    changed = true;
  }
  const localDose = (dashboardData().daily_plan?.doses || []).find((item) => item.id === committed.id);
  if (localDose) {
    localDose.status = committed.status;
    localDose.completed_at = committed.completed_at;
    delete localDose._pending;
    recomputeDoseSummary();
    changed = true;
  }
  return changed;
}

function reconcileCommittedMedication(committed) {
  if (!committed?.id || !state.currentPersonId) return false;
  if (state.dashboardSession.ownerPersonId !== state.currentPersonId) {
    activateDashboardOwner(state.currentPersonId);
  }
  const dashboard = dashboardData() || {};
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
  state.dashboardSession.data = { ...dashboard, medications: next };
  return true;
}

function reconcileCommittedPerson(person, { select = false } = {}) {
  if (!person?.id) return false;
  const affectsCurrentDashboard = select || person.id === state.currentPersonId;
  const exists = state.people.some((item) => item.id === person.id);
  state.people = exists
    ? state.people.map((item) => item.id === person.id ? person : item)
    : [...state.people, person];
  if (select) selectCurrentPerson(person.id);
  return affectsCurrentDashboard;
}

function reconcileDeletedPerson(personId) {
  const deletingCurrent = state.currentPersonId === personId;
  state.people = state.people.filter((person) => person.id !== personId);
  if (!deletingCurrent) return false;
  selectCurrentPerson(state.people[0]?.id || null);
  return Boolean(state.currentPersonId);
}
