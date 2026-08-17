(function attachMedicineOcrReview(root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MedicineOcrReview = api;
})(typeof window === "object" ? window : globalThis, function createMedicineOcrReview(root) {
  "use strict";

  const MAX_ROWS = 24;
  const TIMEOUT_MS = 120000;
  const DRAFT_FIELDS = new Set([
    "dose_amount", "dose_unit", "frequency_per_day", "prescription_days", "schedule_times",
    "meal_relation", "administration_route", "as_needed",
  ]);
  let activeWorker = null;
  let timeout = null;
  let supported = false;

  function normalizeOcrRows(value) {
    if (!Array.isArray(value)) return [];
    return value.slice(0, MAX_ROWS).flatMap((raw, index) => {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
      const productQuery = String(raw.product_query || "").trim().slice(0, 256);
      if (!productQuery) return [];
      const draft = {};
      if (raw.draft && typeof raw.draft === "object" && !Array.isArray(raw.draft)) {
        for (const [key, item] of Object.entries(raw.draft)) {
          if (!DRAFT_FIELDS.has(key)) continue;
          if (key === "schedule_times") {
            if (Array.isArray(item)) draft[key] = item.filter((entry) => typeof entry === "string").slice(0, 24);
          } else if (["dose_amount", "frequency_per_day", "prescription_days"].includes(key)) {
            const number = Number(item);
            if (Number.isFinite(number) && number > 0) draft[key] = number;
          } else if (key === "as_needed") {
            if (item === true) draft[key] = true;
          } else if (typeof item === "string" && item.trim()) {
            draft[key] = item.trim().slice(0, 128);
          }
        }
      }
      const issues = Array.isArray(raw.uncertainty_codes)
        ? [...new Set(raw.uncertainty_codes.filter((item) => typeof item === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(item)))].slice(0, 16)
        : [];
      return [{
        row_id: /^[A-Za-z0-9_-]{1,64}$/.test(String(raw.row_id || "")) ? String(raw.row_id) : `ocr-row-${index + 1}`,
        product_query: productQuery,
        draft,
        uncertainty_codes: issues,
      }];
    });
  }

  function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function unitLabel(value) {
    return { tablet: "정", capsule: "캡슐", packet: "포" }[value] || value || "";
  }

  function option(value, current, label) {
    return `<option value="${value}"${current === value ? " selected" : ""}>${label}</option>`;
  }

  function uncertaintyText(codes) {
    const labels = {
      LOW_CONFIDENCE_OCR: "인식 정확도가 낮아 제품명과 복용 정보를 다시 확인해주세요.",
      UNRESOLVED_REGIMEN_ASSOCIATION: "약 이름과 복용 정보의 연결이 불확실해 직접 확인해주세요.",
      AMBIGUOUS_PRODUCT: "제품명이 여러 방식으로 읽혀 정확한 제품명을 확인해주세요.",
      MISSING_PRODUCT: "제품명을 충분히 읽지 못해 직접 수정해주세요.",
      UNSUPPORTED_AS_NEEDED: "필요시 복용 표현은 자동 확정하지 못해 처방 내용을 확인해주세요.",
      UNSUPPORTED_ROUTE: "투여 경로는 자동 확정하지 못해 처방 내용을 확인해주세요.",
    };
    const messages = [...new Set((codes || []).map((code) => labels[code] || "인식 결과를 직접 확인해주세요."))];
    return messages.join(" ");
  }

  function readRow(card, base) {
    const value = (field) => card.querySelector(`[data-ocr-field="${field}"]`)?.value?.trim() || "";
    const checked = (field) => Boolean(card.querySelector(`[data-ocr-field="${field}"]`)?.checked);
    const number = (field) => {
      const raw = value(field);
      const parsed = raw ? Number(raw) : null;
      return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    };
    const draft = {};
    const amount = number("dose_amount");
    const frequency = number("frequency_per_day");
    const days = number("prescription_days");
    const unit = value("dose_unit");
    const scheduleTimes = value("schedule_times").split(",").map((item) => item.trim()).filter(Boolean).slice(0, 24);
    const mealRelation = value("meal_relation");
    const administrationRoute = value("administration_route");
    if (amount !== null) draft.dose_amount = amount;
    if (unit) draft.dose_unit = unit;
    if (frequency !== null) draft.frequency_per_day = frequency;
    if (days !== null) draft.prescription_days = days;
    if (scheduleTimes.length) draft.schedule_times = scheduleTimes;
    if (mealRelation) draft.meal_relation = mealRelation;
    if (administrationRoute) draft.administration_route = administrationRoute;
    if (checked("as_needed")) draft.as_needed = true;
    return { ...base, product_query: value("product_query"), draft };
  }

  function clearReview() {
    const panel = root.document?.querySelector("#ocr-review-panel");
    const list = root.document?.querySelector("#ocr-review-list");
    if (panel) panel.classList.add("hidden");
    if (list) list.innerHTML = "";
  }

  function renderRows(rows) {
    const panel = root.document.querySelector("#ocr-review-panel");
    const list = root.document.querySelector("#ocr-review-list");
    if (!panel || !list) return;
    if (!rows.length) {
      clearReview();
      return;
    }
    panel.classList.remove("hidden");
    list.innerHTML = rows.map((row, index) => `
      <article class="card ocr-row-card" data-ocr-row="${index}">
        <div class="ocr-row-heading"><strong>인식된 약 ${index + 1}</strong>${row.uncertainty_codes.length ? `<span class="permit-badge unknown">확인 필요</span>` : ""}</div>
        <label>제품명<input data-ocr-field="product_query" value="${escapeHtml(row.product_query)}" autocomplete="off"></label>
        <div class="form-grid two">
          <label>1회 복용량<input data-ocr-field="dose_amount" type="number" min="0" step="0.1" value="${escapeHtml(row.draft.dose_amount ?? "")}"></label>
          <label>단위<input data-ocr-field="dose_unit" value="${escapeHtml(unitLabel(row.draft.dose_unit))}" placeholder="정, mL, 포"></label>
        </div>
        <div class="form-grid two">
          <label>1일 횟수<input data-ocr-field="frequency_per_day" type="number" min="1" max="24" value="${escapeHtml(row.draft.frequency_per_day ?? "")}"></label>
          <label>처방 일수<input data-ocr-field="prescription_days" type="number" min="1" max="3650" value="${escapeHtml(row.draft.prescription_days ?? "")}"></label>
        </div>
        <label>복용 시간<input data-ocr-field="schedule_times" value="${escapeHtml(Array.isArray(row.draft.schedule_times) ? row.draft.schedule_times.join(", ") : "")}" placeholder="08:00, 20:00"></label>
        <div class="form-grid two">
          <label>식사 관계<select data-ocr-field="meal_relation">
            ${option("unspecified", row.draft.meal_relation || "unspecified", "미지정")}
            ${option("after_meal", row.draft.meal_relation, "식후")}
            ${option("before_meal", row.draft.meal_relation, "식전")}
            ${option("with_meal", row.draft.meal_relation, "식사와 함께")}
            ${option("empty_stomach", row.draft.meal_relation, "공복")}
            ${option("regardless", row.draft.meal_relation, "식사 무관")}
          </select></label>
          <label>투여 경로<select data-ocr-field="administration_route">
            ${option("unknown", row.draft.administration_route || "unknown", "확인 필요")}
            ${option("oral", row.draft.administration_route, "경구")}
            ${option("topical", row.draft.administration_route, "외용")}
            ${option("inhaled", row.draft.administration_route, "흡입")}
            ${option("ophthalmic", row.draft.administration_route, "점안")}
            ${option("otic", row.draft.administration_route, "점이")}
            ${option("nasal", row.draft.administration_route, "비강")}
            ${option("injection", row.draft.administration_route, "주사")}
            ${option("other", row.draft.administration_route, "기타")}
          </select></label>
        </div>
        <label class="ocr-choice"><input data-ocr-field="as_needed" type="checkbox"${row.draft.as_needed === true ? " checked" : ""}><span>필요할 때만 복용</span></label>
        ${row.uncertainty_codes.length ? `<p class="muted small ocr-review-note">${escapeHtml(uncertaintyText(row.uncertainty_codes))}</p>` : ""}
        <button class="secondary-button wide" type="button" data-ocr-select="${index}">제품 검색해서 확인</button>
      </article>`).join("");
    list.querySelectorAll("[data-ocr-select]").forEach((button) => {
      button.addEventListener("click", () => {
        const index = Number(button.dataset.ocrSelect);
        const card = list.querySelector(`[data-ocr-row="${index}"]`);
        if (!card) return;
        const reviewed = readRow(card, rows[index]);
        if (!reviewed.product_query) return;
        root.dispatchEvent(new root.CustomEvent("medicine:ocr-select", { detail: reviewed }));
      });
    });
  }

  function setStatus(message) {
    const status = root.document?.querySelector("#ocr-status");
    if (status) status.textContent = message || "";
  }

  function setImportDisabled(disabled, busy = false) {
    const input = root.document?.querySelector("#ocr-image-input");
    const button = root.document?.querySelector(".ocr-file-button");
    if (input) input.disabled = disabled;
    if (!button) return;
    button.classList.toggle("is-disabled", disabled);
    button.setAttribute("aria-disabled", disabled ? "true" : "false");
    if (busy) button.setAttribute("aria-busy", "true");
    else button.removeAttribute("aria-busy");
  }

  function cleanup() {
    if (timeout) root.clearTimeout(timeout);
    timeout = null;
    activeWorker?.terminate();
    activeWorker = null;
    setImportDisabled(!supported);
  }

  function reset() {
    cleanup();
    clearReview();
    setStatus("");
    const input = root.document?.querySelector("#ocr-image-input");
    if (input) input.value = "";
  }

  function recognize(file) {
    cleanup();
    clearReview();
    setImportDisabled(true, true);
    setStatus("사진에서 약 정보를 읽고 있어요… 5%");
    const worker = new Worker("/ocr-assets/direct/ocr-worker.js");
    activeWorker = worker;
    timeout = root.setTimeout(() => {
      if (activeWorker !== worker) return;
      cleanup();
      setStatus("인식 시간이 오래 걸려 중단했어요. 사진을 다시 선택해주세요.");
    }, TIMEOUT_MS);
    worker.onmessage = (event) => {
      if (activeWorker !== worker) return;
      const message = event.data || {};
      if (message.type === "progress") {
        setStatus(`사진에서 약 정보를 읽고 있어요… ${Math.max(0, Math.min(100, Number(message.progress) || 0))}%`);
        return;
      }
      if (message.type === "result") {
        const rows = normalizeOcrRows(message.rows);
        cleanup();
        setStatus(rows.length ? `약 ${rows.length}개를 찾았어요. 제품명과 복용 정보를 확인해주세요.` : "약 정보를 찾지 못했어요.");
        if (rows.length) renderRows(rows);
        else clearReview();
      } else if (message.type === "error") {
        cleanup();
        setStatus("사진을 인식하지 못했어요. 다른 사진을 선택하거나 직접 검색해주세요.");
      }
    };
    worker.onerror = () => {
      if (activeWorker !== worker) return;
      cleanup();
      setStatus("사진 인식 기능을 시작하지 못했어요. 직접 검색해주세요.");
    };
    worker.postMessage({ type: "recognize", image: file });
  }

  function bind() {
    const input = root.document?.querySelector("#ocr-image-input");
    if (!input) return;
    supported = typeof root.Worker === "function" && typeof root.createImageBitmap === "function";
    setImportDisabled(!supported);
    if (!supported) {
      setStatus("이 기기에서는 사진 인식을 사용할 수 없어요.");
      return;
    }
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      input.value = "";
      if (file) recognize(file);
    });
    root.addEventListener("pagehide", reset, { once: false });
  }

  if (root.document) root.document.addEventListener("DOMContentLoaded", bind);
  return { normalizeOcrRows, reset };
});
