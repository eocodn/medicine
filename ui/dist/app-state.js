const state = {
    people: [],
    currentPersonId: localStorage.getItem("medicine.currentPersonId"),
    dashboard: null,
    dashboardDate: null,
    dashboardStale: false,
    fullCatalog: false,
    pendingProduct: null,
    pendingRequestId: null,
    pendingParserDraft: null,
    pendingParserPersonId: null,
    pendingParserUncertaintyCodes: [],
    pendingParserRows: [],
    activeParserRow: null,
    parserRowTotal: 0,
    parserRowIndex: 0,
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
function persistedDoseIntents() {
    const raw = localStorage.getItem(DOSE_INTENTS_STORAGE_KEY);
    if (!raw)
        return {};
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
            throw new Error("invalid dose intents");
        return parsed;
    }
    catch (_) {
        localStorage.removeItem(DOSE_INTENTS_STORAGE_KEY);
        return {};
    }
}
function writePersistedDoseIntents(intents) {
    if (Object.keys(intents).length) {
        localStorage.setItem(DOSE_INTENTS_STORAGE_KEY, JSON.stringify(intents));
    }
    else {
        localStorage.removeItem(DOSE_INTENTS_STORAGE_KEY);
    }
}
function rememberScheduledDoseIntent(instanceId, desiredStatus) {
    const scheduled = (state.dashboard?.daily_plan?.doses || []).some((item) => item.id === instanceId);
    if (!scheduled || !state.currentPersonId)
        return false;
    const intents = persistedDoseIntents();
    intents[instanceId] = { personId: state.currentPersonId, desiredStatus };
    writePersistedDoseIntents(intents);
    return true;
}
function clearScheduledDoseIntent(instanceId) {
    const intents = persistedDoseIntents();
    if (!Object.hasOwn(intents, instanceId))
        return false;
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
        if (requestId)
            state.prnRequests.set(medicationId, requestId);
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
        if (!log?.medication_id || !log?.request_id)
            continue;
        if (pendingPrnRequestId(log.medication_id) === log.request_id) {
            clearPrnRequestId(log.medication_id);
        }
    }
}
function markDashboardStale() {
    state.dashboardStale = true;
    state.dashboardDate = null;
}
function recomputeDoseSummary() {
    const plan = state.dashboard?.daily_plan;
    if (!plan)
        return;
    const doses = plan.doses || [];
    plan.summary = {
        planned: doses.filter((item) => item.status === "planned").length,
        taken: doses.filter((item) => item.status === "taken").length,
        skipped: doses.filter((item) => item.status === "skipped").length,
    };
}
function currentDoseStatus(instanceId) {
    const localDose = (state.dashboard?.daily_plan?.doses || []).find((item) => item.id === instanceId);
    if (localDose)
        return localDose.status;
    const log = (state.dashboard?.recent_logs || []).find((item) => item.dose_instance_id === instanceId);
    return log?.status || null;
}
function applyPendingDoseIntent(instanceId, desiredStatus) {
    const localDose = (state.dashboard?.daily_plan?.doses || []).find((item) => item.id === instanceId);
    if (!localDose)
        return false;
    localDose.status = desiredStatus;
    if (desiredStatus === "planned")
        localDose.completed_at = null;
    localDose._pending = true;
    recomputeDoseSummary();
    return true;
}
function clearPendingDoseIntent(instanceId) {
    const localDose = (state.dashboard?.daily_plan?.doses || []).find((item) => item.id === instanceId);
    if (!localDose)
        return false;
    delete localDose._pending;
    return true;
}
function reconcileDoseMutation(committed) {
    if (!committed || !state.dashboard)
        return false;
    let changed = false;
    if (Array.isArray(committed.recent_logs)) {
        reconcilePrnRequestIds(committed.recent_logs);
        state.dashboard = {
            ...state.dashboard,
            recent_logs: committed.recent_logs,
        };
        changed = true;
    }
    const localDose = (state.dashboard.daily_plan?.doses || []).find((item) => item.id === committed.id);
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
    if (!committed?.id)
        return false;
    const dashboard = state.dashboard || {};
    const medications = dashboard.medications || [];
    const existing = medications.find((item) => item.id === committed.id) || {};
    let next;
    if (committed.active === false) {
        next = medications.filter((item) => item.id !== committed.id);
    }
    else {
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
    if (!person?.id)
        return false;
    const affectsCurrentDashboard = select || person.id === state.currentPersonId;
    const exists = state.people.some((item) => item.id === person.id);
    state.people = exists
        ? state.people.map((item) => item.id === person.id ? person : item)
        : [...state.people, person];
    if (select) {
        if (state.currentPersonId && state.currentPersonId !== person.id && typeof resetParserTransientState === "function") {
            resetParserTransientState({ clearSearch: true });
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
    if (!deletingCurrent)
        return false;
    if (typeof resetParserTransientState === "function")
        resetParserTransientState({ clearSearch: true });
    state.currentPersonId = state.people[0]?.id || null;
    state.dashboard = null;
    state.dashboardDate = null;
    if (state.currentPersonId)
        localStorage.setItem("medicine.currentPersonId", state.currentPersonId);
    else {
        localStorage.removeItem("medicine.currentPersonId");
        state.dashboardStale = false;
    }
    return Boolean(state.currentPersonId);
}
