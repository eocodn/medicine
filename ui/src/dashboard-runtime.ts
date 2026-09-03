async function loadDashboard() {
  const personId = state.currentPersonId;
  if (!personId) {
    clearDashboardSession();
    return;
  }
  const targetDate = todayInKorea();
  const existing = state.dashboardLoads.get(personId);
  if (existing) {
    if (
      existing.targetDate === targetDate &&
      existing.generation === state.dashboardSession.generation
    ) {
      existing.dirty = true;
      return existing.promise;
    }
    // The active session was invalidated while this request was in flight.
    // Let that obsolete request drain, then perform a fresh authoritative load.
    return existing.promise.catch(() => undefined).then(() => loadDashboard());
  }
  const generation = beginDashboardLoad(personId, targetDate);
  const entry = { dirty: false, promise: null, generation, targetDate };
  entry.promise = (async () => {
    try {
      do {
        entry.dirty = false;
        const dashboard = await api(`/api/people/${personId}/dashboard`);
        if (!entry.dirty) commitDashboardLoad(personId, targetDate, generation, dashboard);
      } while (entry.dirty && state.currentPersonId === personId && state.dashboardSession.generation === generation);
    } catch (error) {
      failDashboardLoad(personId, targetDate, generation, error);
      throw error;
    } finally {
      if (state.dashboardLoads.get(personId) === entry) state.dashboardLoads.delete(personId);
    }
  })();
  state.dashboardLoads.set(personId, entry);
  return entry.promise;
}

async function refreshActiveDashboard({ recoverDoseIntents = true } = {}) {
  if (!state.currentPersonId) return;
  try {
    await loadDashboard();
    if (recoverDoseIntents && dashboardReadyForCurrent()) recoverPersistedDoseIntents(state.currentPersonId);
  } finally {
    renderAll();
  }
}

async function refreshForDateChange() {
  if (!state.currentPersonId || document.visibilityState === "hidden") return;
  if (dashboardReadyForCurrent()) return;
  const currentDate = todayInKorea();
  if (
    state.dashboardSession.ownerPersonId === state.currentPersonId &&
    state.dashboardSession.date === currentDate &&
    state.dashboardSession.phase === "loading"
  ) return;
  const dateChanged = state.dashboardSession.date !== currentDate;
  if (dateChanged) markDashboardStale("date_changed");
  renderAll();
  try {
    await refreshActiveDashboard();
  } catch (error) {
    console.error("dashboard refresh failed", error);
  }
}

async function refreshPersonalData() {
  if (!state.currentPersonId) return;
  markDashboardStale("external_personal_write");
  renderAll();
  try {
    await refreshActiveDashboard();
  } catch (error) {
    console.error("personal data refresh failed", error);
  }
}

window.MedicineApp = { refreshPersonalData };
