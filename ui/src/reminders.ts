(function attachReminderUi() {
  type ReminderStatus = {
    supported: boolean;
    enabled: boolean;
    notifications_allowed: boolean;
    exact_alarm_allowed: boolean;
  };

  function rootNode(): HTMLElement | null {
    return document.getElementById("reminder-settings");
  }

  function readStatus(): ReminderStatus | null {
    const native = window.MedicineReminderNative;
    if (!native || typeof native.status !== "function") return null;
    try {
      return JSON.parse(native.status()) as ReminderStatus;
    } catch (_) {
      return null;
    }
  }

  function refresh() {
    const root = rootNode();
    if (!root) return;
    const status = readStatus();
    if (!status?.supported) {
      root.classList.add("hidden");
      root.innerHTML = "";
      return;
    }
    root.classList.remove("hidden");

    let stateCopy = "복용 시간을 저장하면 모든 프로필의 약을 정해진 시간에 알려드려요.";
    let primaryLabel = "복약 알림 켜기";
    if (status.enabled && !status.notifications_allowed) {
      stateCopy = "복약 알림은 켜져 있지만 휴대폰 알림 권한이 꺼져 있어요.";
      primaryLabel = "알림 권한 허용";
    } else if (status.enabled && !status.exact_alarm_allowed) {
      stateCopy = "복약 알림은 켜져 있어요. 정확한 시간 권한을 허용하면 지연 가능성을 줄일 수 있어요.";
      primaryLabel = "복약 알림 끄기";
    } else if (status.enabled) {
      stateCopy = "모든 프로필의 시간 지정 약을 복용 시간에 맞춰 알려드려요.";
      primaryLabel = "복약 알림 끄기";
    }

    root.innerHTML = `
      <article class="card reminder-settings-card">
        <div>
          <p class="eyebrow">REMINDERS</p>
          <h3>복약 알림</h3>
          <p class="muted small">${stateCopy}</p>
        </div>
        <div class="reminder-settings-actions">
          <button class="secondary-button" data-reminder-toggle type="button">${primaryLabel}</button>
          ${status.enabled && status.notifications_allowed && !status.exact_alarm_allowed
            ? `<button class="secondary-button" data-reminder-exact type="button">정확한 시간 알림 허용</button>`
            : ""}
        </div>
      </article>`;

    root.querySelector("[data-reminder-toggle]")?.addEventListener("click", () => {
      if (!status.enabled || !status.notifications_allowed) {
        window.MedicineReminderNative?.setEnabled(true);
      } else {
        window.MedicineReminderNative?.setEnabled(false);
      }
      window.setTimeout(refresh, 0);
    });
    root.querySelector("[data-reminder-exact]")?.addEventListener("click", () => {
      window.MedicineReminderNative?.requestExactAlarmAccess();
    });
  }

  function offerAfterScheduledMedicationSave(scheduleTimes: unknown) {
    if (!Array.isArray(scheduleTimes) || scheduleTimes.length === 0) return;
    window.MedicineReminderNative?.offerAfterScheduledMedicationSave();
    window.setTimeout(refresh, 0);
  }

  window.MedicineReminderUi = { refresh, offerAfterScheduledMedicationSave };
  document.addEventListener("DOMContentLoaded", refresh);
})();
