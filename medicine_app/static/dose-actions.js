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
      personId: state.currentPersonId,
      running: false,
    };
    state.doseMutations.set(instanceId, entry);
  }
  entry.desiredStatus = desiredStatus;
  if (applyPendingDoseIntent(instanceId, desiredStatus)) renderHome();
  if (!entry.running) void drainDoseDesiredState(instanceId, entry);
}

async function drainDoseDesiredState(instanceId, entry) {
  if (state.doseMutations.get(instanceId) !== entry || entry.running) return;
  if (entry.authoritativeStatus === entry.desiredStatus) {
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
    if (entry.desiredStatus !== requestedStatus) {
      void drainDoseDesiredState(instanceId, entry);
      return;
    }
    state.doseMutations.delete(instanceId);
    if (state.currentPersonId === entry.personId) {
      try {
        await loadDashboard();
      } catch (refreshError) {
        console.error("dashboard refresh after dose mutation failure failed", refreshError);
        markDashboardStale();
      }
      renderAll();
      toast(error.message);
    }
    return;
  }

  entry.running = false;
  entry.authoritativeStatus = updated.status;
  if (state.currentPersonId !== entry.personId) {
    state.doseMutations.delete(instanceId);
    return;
  }
  reconcileDoseMutation(updated);
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

function completeDoseInstance(instanceId, status, button = null) {
  queueDoseDesiredState(instanceId, status, button);
}

function cancelDoseInstance(instanceId, button = null) {
  queueDoseDesiredState(instanceId, "planned", button);
}