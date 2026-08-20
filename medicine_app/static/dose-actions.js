function newPrnRequestId() {
  if (typeof crypto?.randomUUID === "function") return `prn-${crypto.randomUUID()}`;
  return `prn-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function recordPrnIntake(medicationId, button = null) {
  const personId = state.currentPersonId;
  const originalText = button?.textContent || "";
  let requestId = pendingPrnRequestId(medicationId);
  if (!requestId) {
    requestId = newPrnRequestId();
    rememberPrnRequestId(medicationId, requestId);
  }
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "처리 중…";
  }
  try {
    const updated = await api(`/api/medications/${medicationId}/prn-intakes`, {
      method: "POST",
      body: JSON.stringify({ request_id: requestId }),
    });
    clearPrnRequestId(medicationId);
    if (state.currentPersonId !== personId) return;
    reconcileDoseMutation(updated);
    renderAll();
    toast("필요시 복용을 기록했어요");
  } catch (error) {
    if (error?.status && error.status < 500) clearPrnRequestId(medicationId);
    if (state.currentPersonId !== personId) return;
    if (button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.textContent = originalText;
    }
    toast(error.message);
  }
}

function queueDoseDesiredState(instanceId, desiredStatus, button = null) {
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "처리 중…";
  }
  let entry = state.doseMutations.get(instanceId);
  if (!entry) {
    entry = {
      authoritativeStatus: currentDoseStatus(instanceId),
      ambiguousCompensations: 0,
      personId: state.currentPersonId,
      running: false,
    };
    state.doseMutations.set(instanceId, entry);
  }
  entry.desiredStatus = desiredStatus;
  rememberScheduledDoseIntent(instanceId, desiredStatus);
  if (applyPendingDoseIntent(instanceId, desiredStatus)) renderHome();
  if (!entry.running) void drainDoseDesiredState(instanceId, entry);
}

async function drainDoseDesiredState(instanceId, entry) {
  if (state.doseMutations.get(instanceId) !== entry || entry.running) return;
  if (entry.authoritativeStatus === entry.desiredStatus) {
    clearScheduledDoseIntent(instanceId);
    clearPendingDoseIntent(instanceId);
    state.doseMutations.delete(instanceId);
    renderAll();
    return;
  }

  const requestedStatus = entry.desiredStatus;
  entry.running = true;
  let updated;
  try {
    if (requestedStatus === "planned") {
      updated = await api(`/api/dose-instances/${instanceId}/completion`, { method: "DELETE" });
    } else {
      updated = await api(`/api/dose-instances/${instanceId}`, {
        method: "POST",
        body: JSON.stringify({ status: requestedStatus }),
      });
    }
  } catch (error) {
    entry.running = false;
    if (state.doseMutations.get(instanceId) !== entry) return;
    const status = Number(error?.status);
    const commitMayHaveSucceeded = !Number.isFinite(status) || status >= 500;
    if (!commitMayHaveSucceeded && entry.desiredStatus === requestedStatus) {
      clearScheduledDoseIntent(instanceId);
    }
    if (state.currentPersonId !== entry.personId) {
      state.doseMutations.delete(instanceId);
      return;
    }
    if (commitMayHaveSucceeded) {
      // Reconciliation is part of the same mutation boundary. Keep subsequent
      // taps queued as desired-state changes until authoritative state is known.
      entry.running = true;
      try {
        await loadDashboard();
      } catch (refreshError) {
        console.error("dashboard refresh after ambiguous dose mutation failure failed", refreshError);
        entry.running = false;
        state.doseMutations.delete(instanceId);
        markDashboardStale();
        renderAll();
        toast(error.message);
        return;
      }
      if (state.doseMutations.get(instanceId) !== entry) return;
      if (state.currentPersonId !== entry.personId) {
        state.doseMutations.delete(instanceId);
        return;
      }
      entry.running = false;
      const refreshedStatus = currentDoseStatus(instanceId);
      if (!refreshedStatus) {
        // A successful dashboard refresh is authoritative only for dose instances
        // represented in its current plan or recent log window. If this instance
        // is absent, do not guess whether the failed write committed.
        state.doseMutations.delete(instanceId);
        markDashboardStale();
        renderAll();
        toast(error.message);
        return;
      }
      entry.authoritativeStatus = refreshedStatus;
      if (entry.authoritativeStatus !== entry.desiredStatus) {
        if (entry.ambiguousCompensations >= 1) {
          clearScheduledDoseIntent(instanceId);
          clearPendingDoseIntent(instanceId);
          state.doseMutations.delete(instanceId);
          renderAll();
          toast(error.message);
          return;
        }
        entry.ambiguousCompensations += 1;
        applyPendingDoseIntent(instanceId, entry.desiredStatus);
        renderHome();
        void drainDoseDesiredState(instanceId, entry);
        return;
      }
      clearScheduledDoseIntent(instanceId);
      clearPendingDoseIntent(instanceId);
      state.doseMutations.delete(instanceId);
      renderAll();
      return;
    }
    if (entry.desiredStatus !== requestedStatus) {
      void drainDoseDesiredState(instanceId, entry);
      return;
    }
    clearScheduledDoseIntent(instanceId);
    state.doseMutations.delete(instanceId);
    try {
      await loadDashboard();
    } catch (refreshError) {
      console.error("dashboard refresh after dose mutation failure failed", refreshError);
      markDashboardStale();
    }
    renderAll();
    toast(error.message);
    return;
  }

  entry.running = false;
  entry.authoritativeStatus = updated.status;
  const converged = updated.deleted === true || entry.desiredStatus === updated.status;
  if (converged) clearScheduledDoseIntent(instanceId);
  if (state.currentPersonId !== entry.personId) {
    state.doseMutations.delete(instanceId);
    return;
  }
  reconcileDoseMutation(updated);
  if (updated.deleted === true) {
    clearPendingDoseIntent(instanceId);
    state.doseMutations.delete(instanceId);
    renderAll();
    return;
  }
  if (entry.desiredStatus !== updated.status) {
    applyPendingDoseIntent(instanceId, entry.desiredStatus);
    renderHome();
    void drainDoseDesiredState(instanceId, entry);
    return;
  }
  clearPendingDoseIntent(instanceId);
  state.doseMutations.delete(instanceId);
  renderAll();
}

function recoverPersistedDoseIntents(personId = state.currentPersonId) {
  if (!personId || state.currentPersonId !== personId) return;
  const intents = persistedDoseIntents();
  for (const [instanceId, intent] of Object.entries(intents)) {
    if (intent?.personId !== personId) continue;
    if (!["planned", "taken", "skipped"].includes(intent.desiredStatus)) {
      clearScheduledDoseIntent(instanceId);
      continue;
    }
    const dose = (state.dashboard?.daily_plan?.doses || []).find((item) => item.id === instanceId);
    if (!dose) continue;
    if (dose.status === intent.desiredStatus) {
      clearScheduledDoseIntent(instanceId);
      continue;
    }
    queueDoseDesiredState(instanceId, intent.desiredStatus);
  }
}

function completeDoseInstance(instanceId, status, button = null) {
  queueDoseDesiredState(instanceId, status, button);
}

function cancelDoseInstance(instanceId, button = null) {
  queueDoseDesiredState(instanceId, "planned", button);
}