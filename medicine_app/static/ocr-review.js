(function attachMedicineOcrReview(root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MedicineOcrReview = api;
})(typeof window === "object" ? window : globalThis, function createMedicineOcrReview(global) {
  "use strict";

  const EDITABLE_FIELDS = new Set([
    "product_query", "dose_amount", "dose_unit", "frequency_per_day", "prescription_days",
    "schedule_times", "meal_relation", "administration_route", "as_needed", "start_date",
  ]);
  // `generation` identifies the exact editable snapshot that an async preview/create belongs to.
  const state = {
    rows: [], operationId: null, requestId: null, issues: null,
    phase: "idle", warningToken: null, ocrReviewToken: null, busy: false, finalizing: false,
    generation: 0, activeSubmissionId: null,
  };
  let config = {};
  let nextSubmissionId = 0;
  const searchEpochs = new Map();

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function inputValue(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function normalizeRows(rows, today) {
    const seen = new Set();
    return (Array.isArray(rows) ? rows : []).slice(0, 24).map((source, index) => {
      let rowId = typeof source?.row_id === "string" ? source.row_id.trim() : "";
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(rowId) || seen.has(rowId)) rowId = `ocr-${index + 1}`;
      while (seen.has(rowId)) rowId = `${rowId}-${index + 1}`;
      seen.add(rowId);
      return {
        row_id: rowId,
        product_query: inputValue(source?.product_query || source?.product_name),
        product_ref: typeof source?.product_ref === "string" && source.product_ref ? source.product_ref : null,
        selected_name: typeof source?.product_ref === "string" && source.product_ref ? inputValue(source?.product_query || source?.product_name) : null,
        dose_amount: inputValue(source?.dose_amount),
        dose_unit: inputValue(source?.dose_unit),
        frequency_per_day: inputValue(source?.frequency_per_day),
        prescription_days: inputValue(source?.prescription_days),
        schedule_times: Array.isArray(source?.schedule_times) ? source.schedule_times.join(", ") : inputValue(source?.schedule_times),
        meal_relation: inputValue(source?.meal_relation) || "unspecified",
        administration_route: inputValue(source?.administration_route) || "unknown",
        // Only product-derived defaults are invalidated when product identity changes.
        route_from_product: false,
        as_needed: Boolean(source?.as_needed),
        start_date: inputValue(source?.start_date) || today,
        association: inputValue(source?.association) || "unresolved",
        candidates: [],
        assessment: null,
      };
    });
  }

  function numberOrNull(value) {
    const text = String(value ?? "").trim();
    if (!text) return null;
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : text;
  }

  function payloadRows() {
    return state.rows.map((row) => ({
      row_id: row.row_id,
      product_ref: row.product_ref,
      dose_amount: numberOrNull(row.dose_amount),
      dose_unit: String(row.dose_unit || "").trim() || null,
      frequency_per_day: numberOrNull(row.frequency_per_day),
      prescription_days: numberOrNull(row.prescription_days),
      schedule_times: String(row.schedule_times || "").split(",").map((item) => item.trim()).filter(Boolean),
      meal_relation: row.meal_relation || "unspecified",
      administration_route: row.administration_route || "unknown",
      as_needed: Boolean(row.as_needed),
      start_date: row.start_date || null,
    }));
  }

  function reset() {
    state.generation += 1;
    state.activeSubmissionId = null;
    state.rows = [];
    state.operationId = null;
    state.requestId = null;
    state.issues = null;
    state.phase = "idle";
    state.warningToken = null;
    state.ocrReviewToken = null;
    state.busy = false;
    state.finalizing = false;
    searchEpochs.clear();
  }

  function init(options = {}) {
    config = { ...options };
    reset();
  }

  function invalidateReview() {
    state.generation += 1;
    state.phase = "editing";
    state.warningToken = null;
    state.ocrReviewToken = null;
    state.rows.forEach((row) => { row.assessment = null; });
  }

  function rowById(rowId) {
    return state.rows.find((row) => row.row_id === rowId) || null;
  }

  function updateField(rowId, field, value) {
    if (state.finalizing || !EDITABLE_FIELDS.has(field)) return false;
    const row = rowById(rowId);
    if (!row) return false;
    row[field] = field === "as_needed" ? Boolean(value) : inputValue(value);
    if (field === "administration_route") row.route_from_product = false;
    if (field === "product_query") {
      row.product_ref = null;
      row.selected_name = null;
      row.candidates = [];
      if (row.route_from_product) {
        row.administration_route = "unknown";
        row.route_from_product = false;
      }
      searchEpochs.set(rowId, (searchEpochs.get(rowId) || 0) + 1);
    }
    invalidateReview();
    return true;
  }

  function selectCandidate(row, candidate, options = {}) {
    const rerender = options.rerender !== false;
    if (state.finalizing || !row || !candidate?.product_ref) return false;
    if (row.route_from_product) {
      row.administration_route = "unknown";
      row.route_from_product = false;
    }
    row.product_ref = candidate.product_ref;
    row.selected_name = candidate.product_name || row.product_query;
    row.product_query = candidate.product_name || row.product_query;
    const route = candidate.suggested_administration_route;
    if ((!row.administration_route || row.administration_route === "unknown") && route && route !== "unknown") {
      row.administration_route = route;
      row.route_from_product = true;
    }
    invalidateReview();
    if (rerender) render();
    return true;
  }

  async function searchRow(rowId) {
    if (state.finalizing) return [];
    const row = rowById(rowId);
    const query = String(row?.product_query || "").trim();
    if (!row || !query || typeof config.api !== "function") return [];
    const epoch = (searchEpochs.get(rowId) || 0) + 1;
    searchEpochs.set(rowId, epoch);
    const products = await config.api(`/api/products?q=${encodeURIComponent(query)}&limit=8`);
    if (state.finalizing || rowById(rowId) !== row || searchEpochs.get(rowId) !== epoch || String(row.product_query || "").trim() !== query) return [];
    row.candidates = Array.isArray(products) ? products : [];
    const exact = row.candidates.filter((item) => String(item.product_name || "").trim() === query);
    if (exact.length === 1) selectCandidate(row, exact[0], { rerender: false });
    render();
    return row.candidates;
  }

  async function resolveInitialProducts() {
    await Promise.all(state.rows.filter((row) => !row.product_ref && row.product_query).map((row) => searchRow(row.row_id)));
  }

  function assessmentHtml(row) {
    if (!row.assessment) return "";
    const renderDur = typeof config.renderDur === "function" ? config.renderDur : () => "";
    return `<div class="ocr-row-assessment"><strong>DUR 확인 결과</strong>${renderDur(row.assessment.dur_checks || [])}</div>`;
  }

  function candidateHtml(row) {
    const disabled = state.finalizing ? " disabled" : "";
    if (row.product_ref) {
      return `<div class="ocr-selected-product">선택됨 · ${escapeHtml(row.selected_name || row.product_query)}</div>`;
    }
    if (!row.candidates.length) return `<div class="ocr-product-help">약명을 확인하고 ‘찾기’를 눌러 제품을 선택해주세요.</div>`;
    return `<div class="ocr-product-candidates">${row.candidates.map((candidate) => `
      <button type="button" data-ocr-candidate="${escapeHtml(candidate.product_ref)}" data-ocr-row="${escapeHtml(row.row_id)}"${disabled}>
        <strong>${escapeHtml(candidate.product_name)}</strong><span>${escapeHtml(candidate.manufacturer || "")}</span>
      </button>`).join("")}</div>`;
  }

  function option(value, current, label) {
    return `<option value="${value}"${current === value ? " selected" : ""}>${label}</option>`;
  }

  function rowHtml(row, index) {
    const disabled = state.finalizing ? " disabled" : "";
    const associationCopy = {
      table_row: "처방전 같은 행에서 읽음",
      labeled_block: "약봉투의 같은 약 블록에서 읽음",
      group_shared: "명시된 공통 복용법을 적용함",
      single_document: "한 약 처방에서 읽음",
      unresolved: "약과 복용법 연결을 직접 확인해주세요",
    }[row.association] || "OCR 인식값을 확인해주세요";
    return `<article class="ocr-review-row" data-ocr-row-card="${escapeHtml(row.row_id)}">
      <div class="ocr-row-heading"><div><span>약 ${index + 1}</span><small>${escapeHtml(associationCopy)}</small></div>
        ${state.rows.length > 1 ? `<button type="button" class="text-button" data-ocr-remove="${escapeHtml(row.row_id)}"${disabled}>행 삭제</button>` : ""}</div>
      <label class="ocr-field-wide">약 제품
        <div class="ocr-product-search"><input data-ocr-field="product_query" data-ocr-row="${escapeHtml(row.row_id)}" value="${escapeHtml(row.product_query)}" placeholder="제품명"${disabled}><button type="button" data-ocr-search="${escapeHtml(row.row_id)}"${disabled}>찾기</button></div>
      </label>
      ${candidateHtml(row)}
      <div class="ocr-field-grid">
        <label>1회량<input type="number" min="0" step="0.1" data-ocr-field="dose_amount" data-ocr-row="${escapeHtml(row.row_id)}" value="${escapeHtml(row.dose_amount)}"${disabled}></label>
        <label>단위<input data-ocr-field="dose_unit" data-ocr-row="${escapeHtml(row.row_id)}" value="${escapeHtml(row.dose_unit)}" placeholder="정, 캡슐, mL"${disabled}></label>
        <label>1일 횟수<input type="number" min="1" max="24" data-ocr-field="frequency_per_day" data-ocr-row="${escapeHtml(row.row_id)}" value="${escapeHtml(row.frequency_per_day)}"${disabled}></label>
        <label>처방 일수<input type="number" min="1" max="3650" data-ocr-field="prescription_days" data-ocr-row="${escapeHtml(row.row_id)}" value="${escapeHtml(row.prescription_days)}"${disabled}></label>
      </div>
      <label class="ocr-field-wide">복용·사용 시간<input data-ocr-field="schedule_times" data-ocr-row="${escapeHtml(row.row_id)}" value="${escapeHtml(row.schedule_times)}" placeholder="08:00, 20:00"${disabled}></label>
      <div class="ocr-field-grid">
        <label>식사 관계<select data-ocr-field="meal_relation" data-ocr-row="${escapeHtml(row.row_id)}"${disabled}>
          ${option("unspecified", row.meal_relation, "미지정")}${option("after_meal", row.meal_relation, "식후")}${option("before_meal", row.meal_relation, "식전")}${option("with_meal", row.meal_relation, "식사와 함께")}${option("empty_stomach", row.meal_relation, "공복")}${option("regardless", row.meal_relation, "식사 무관")}
        </select></label>
        <label>투여 경로<select data-ocr-field="administration_route" data-ocr-row="${escapeHtml(row.row_id)}"${disabled}>
          ${option("unknown", row.administration_route, "확인 필요")}${option("oral", row.administration_route, "경구")}${option("topical", row.administration_route, "외용")}${option("inhaled", row.administration_route, "흡입")}${option("ophthalmic", row.administration_route, "점안")}${option("otic", row.administration_route, "점이")}${option("nasal", row.administration_route, "비강")}${option("injection", row.administration_route, "주사")}${option("other", row.administration_route, "기타")}
        </select></label>
      </div>
      <label class="ocr-field-wide">시작일<input type="date" data-ocr-field="start_date" data-ocr-row="${escapeHtml(row.row_id)}" value="${escapeHtml(row.start_date)}"${disabled}></label>
      <label class="ocr-prn"><input type="checkbox" data-ocr-field="as_needed" data-ocr-row="${escapeHtml(row.row_id)}"${row.as_needed ? " checked" : ""}${disabled}><span>필요할 때만 사용</span></label>
      ${assessmentHtml(row)}
    </article>`;
  }

  function bindEvents(rootNode) {
    rootNode.querySelectorAll?.("[data-ocr-field]").forEach((node) => {
      const eventName = node.type === "checkbox" || node.tagName === "SELECT" ? "change" : "input";
      node.addEventListener(eventName, () => {
        updateField(node.dataset.ocrRow, node.dataset.ocrField, node.type === "checkbox" ? node.checked : node.value);
        rootNode.querySelectorAll?.(".ocr-row-assessment").forEach((item) => item.remove());
        const submit = rootNode.querySelector?.("#ocr-batch-submit");
        if (submit) submit.textContent = "전체 확인하고 일괄 등록";
      });
    });
    rootNode.querySelectorAll?.("[data-ocr-search]").forEach((node) => node.addEventListener("click", () => {
      void searchRow(node.dataset.ocrSearch).catch((error) => config.toast?.(error.message));
    }));
    rootNode.querySelectorAll?.("[data-ocr-candidate]").forEach((node) => node.addEventListener("click", () => {
      const row = rowById(node.dataset.ocrRow);
      const candidate = row?.candidates.find((item) => item.product_ref === node.dataset.ocrCandidate);
      if (row && candidate) selectCandidate(row, candidate);
    }));
    rootNode.querySelectorAll?.("[data-ocr-remove]").forEach((node) => node.addEventListener("click", () => {
      if (state.finalizing) return;
      state.rows = state.rows.filter((row) => row.row_id !== node.dataset.ocrRemove);
      invalidateReview();
      render();
    }));
    rootNode.querySelector?.("#ocr-add-row")?.addEventListener("click", () => {
      if (state.finalizing) return;
      const next = normalizeRows([{}], typeof config.today === "function" ? config.today() : "")[0];
      let index = state.rows.length + 1;
      while (state.rows.some((row) => row.row_id === `ocr-${index}`)) index += 1;
      next.row_id = `ocr-${index}`;
      state.rows.push(next);
      invalidateReview();
      render();
    });
    rootNode.querySelector?.("#ocr-batch-submit")?.addEventListener("click", () => {
      void submit().catch((error) => config.toast?.(error.message));
    });
    rootNode.querySelector?.("#ocr-batch-cancel")?.addEventListener("click", () => {
      if (!state.finalizing) config.close?.();
    });
  }

  function render() {
    const rootNode = config.root || global.document?.querySelector?.("#ocr-review-content");
    if (!rootNode) return;
    const messages = state.issues?.messages || [];
    rootNode.innerHTML = `
      <div class="sheet-header ocr-review-header"><div><p class="eyebrow">OCR REVIEW</p><h2 id="ocr-review-title">인식한 처방을 확인해주세요</h2></div><button type="button" class="icon-button" id="ocr-batch-cancel"${state.finalizing ? " disabled" : ""}>×</button></div>
      <p class="muted small ocr-review-intro">약별로 인식한 값을 모두 수정할 수 있어요. 전체 내용을 확인한 뒤 한 번에 등록합니다.</p>
      ${messages.map((message) => `<div class="coverage-note limited">${escapeHtml(message)}</div>`).join("")}
      <div class="ocr-review-list">${state.rows.map(rowHtml).join("")}</div>
      <button type="button" class="secondary-button wide" id="ocr-add-row"${state.finalizing ? " disabled" : ""}>+ 약 한 줄 추가</button>
      <div class="ocr-batch-actions"><button type="button" class="primary-button wide" id="ocr-batch-submit" ${state.busy ? "disabled" : ""}>${state.phase === "reviewed" ? "주의사항을 확인했고 전부 등록" : "전체 확인하고 일괄 등록"}</button></div>
      <p class="risk-disclaimer">OCR 인식값은 저장 전에 직접 확인해주세요. 일괄 등록은 모든 행이 함께 성공하거나 모두 저장되지 않습니다.</p>`;
    bindEvents(rootNode);
  }

  function open(rows, operationId, issues) {
    if (state.finalizing) return getState();
    const today = typeof config.today === "function" ? config.today() : "";
    state.generation += 1;
    state.activeSubmissionId = null;
    searchEpochs.clear();
    state.rows = normalizeRows(rows, today);
    if (!state.rows.length) state.rows = normalizeRows([{}], today);
    state.operationId = String(operationId || "").trim();
    state.requestId = typeof config.randomId === "function" ? config.randomId() : (global.crypto?.randomUUID?.() || `ocr-${Date.now()}`);
    state.issues = issues || null;
    state.phase = "editing";
    state.warningToken = null;
    state.ocrReviewToken = null;
    state.busy = false;
    state.finalizing = false;
    render();
    config.open?.();
    void resolveInitialProducts().catch((error) => config.toast?.(error.message));
    return getState();
  }

  function applyPreview(preview) {
    state.warningToken = preview?.warning_token || null;
    state.ocrReviewToken = preview?.ocr_review_token || null;
    const reviewedRows = new Map((preview?.rows || []).map((row) => [row.row_id, row]));
    state.rows.forEach((row) => {
      const reviewed = reviewedRows.get(row.row_id);
      row.assessment = reviewed?.assessment || null;
      const draft = reviewed?.draft;
      if (!draft) return;
      for (const key of ["dose_amount", "dose_unit", "frequency_per_day", "prescription_days", "meal_relation", "administration_route", "start_date"]) {
        if (draft[key] !== null && draft[key] !== undefined) row[key] = inputValue(draft[key]);
      }
      if (Array.isArray(draft.schedule_times)) row.schedule_times = draft.schedule_times.join(", ");
      row.as_needed = Boolean(draft.as_needed);
    });
    state.phase = preview?.requires_review ? "reviewed" : "ready";
  }

  function validateSelections() {
    if (!state.rows.length) return "등록할 약이 없어요.";
    for (let index = 0; index < state.rows.length; index += 1) {
      const row = state.rows[index];
      const prefix = `약 ${index + 1}`;
      if (!row.product_ref) return `${prefix}의 제품을 먼저 선택해주세요.`;
      const amount = String(row.dose_amount || "").trim();
      if (amount && (!Number.isFinite(Number(amount)) || Number(amount) <= 0)) return `${prefix}의 1회량을 확인해주세요.`;
      const frequency = String(row.frequency_per_day || "").trim();
      if (frequency && (!Number.isInteger(Number(frequency)) || Number(frequency) < 1 || Number(frequency) > 24)) return `${prefix}의 1일 횟수를 확인해주세요.`;
      const days = String(row.prescription_days || "").trim();
      if (days && (!Number.isInteger(Number(days)) || Number(days) < 1 || Number(days) > 3650)) return `${prefix}의 처방 일수를 확인해주세요.`;
      const times = String(row.schedule_times || "").split(",").map((item) => item.trim()).filter(Boolean);
      if (times.some((time) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) || new Set(times).size !== times.length) return `${prefix}의 복용 시간을 08:00 형식으로 확인해주세요.`;
      if (frequency && times.length && Number(frequency) !== times.length) return `${prefix}의 1일 횟수와 복용 시간 개수가 맞는지 확인해주세요.`;
    }
    return null;
  }

  function isCurrentSubmission(submissionId, generation) {
    return state.activeSubmissionId === submissionId && state.generation === generation;
  }

  async function createReviewedBatch(personId, rows, submissionId, generation) {
    if (!isCurrentSubmission(submissionId, generation)) return { created: false, stale: true };
    const body = {
      request_id: state.requestId,
      rows,
      ocr_review_token: state.ocrReviewToken,
      acknowledge_warnings: Boolean(state.warningToken),
      warning_token: state.warningToken,
    };
    state.finalizing = true;
    render();
    let result;
    try {
      result = await config.api(`/api/people/${personId}/medications/batch`, { method: "POST", body: JSON.stringify(body) });
    } catch (error) {
      if (!isCurrentSubmission(submissionId, generation)) return { created: false, stale: true };
      state.finalizing = false;
      if (error?.status === 409 && error.body?.confirmation_required && error.body?.assessment) {
        applyPreview(error.body.assessment);
        render();
        return { created: false, reviewRequired: true };
      }
      if (error?.status === 400 && String(error.message || "").includes("ocr_review_token")) {
        invalidateReview();
        render();
        config.toast?.("처방 확인 시간이 지나 전체 내용을 다시 확인합니다.");
        return { created: false, reviewExpired: true };
      }
      throw error;
    }
    if (!isCurrentSubmission(submissionId, generation)) return { created: false, stale: true, result };
    reset();
    if (typeof config.onCreated === "function") await config.onCreated(result);
    return { created: true, result };
  }

  async function submit() {
    if (state.busy) return { busy: true };
    const selectionError = validateSelections();
    if (selectionError) {
      config.toast?.(selectionError);
      return { created: false, invalid: true };
    }
    const personId = typeof config.currentPersonId === "function" ? config.currentPersonId() : null;
    if (!personId) {
      config.toast?.("먼저 관리 대상을 선택해주세요.");
      return { created: false, invalid: true };
    }
    const submissionId = ++nextSubmissionId;
    const generation = state.generation;
    state.activeSubmissionId = submissionId;
    state.busy = true;
    render();
    try {
      const rows = payloadRows();
      if (state.phase !== "reviewed" && state.phase !== "ready") {
        let preview;
        try {
          preview = await config.api(`/api/people/${personId}/medications/batch-preview`, {
            method: "POST",
            body: JSON.stringify({ operation_id: state.operationId, rows }),
          });
        } catch (error) {
          if (!isCurrentSubmission(submissionId, generation)) return { created: false, stale: true };
          throw error;
        }
        if (!isCurrentSubmission(submissionId, generation)) return { created: false, stale: true };
        applyPreview(preview);
        render();
        if (preview.requires_review) {
          config.toast?.("약별 DUR 주의사항을 확인해주세요.");
          return { created: false, reviewRequired: true };
        }
      }
      return await createReviewedBatch(personId, rows, submissionId, generation);
    } finally {
      if (state.activeSubmissionId === submissionId) {
        state.activeSubmissionId = null;
        state.busy = false;
        state.finalizing = false;
        if (state.phase !== "idle") render();
      }
    }
  }

  function getState() {
    return {
      rows: state.rows.map((row) => ({ ...row, candidates: row.candidates.slice() })),
      operationId: state.operationId,
      requestId: state.requestId,
      phase: state.phase,
      warningToken: state.warningToken,
      ocrReviewToken: state.ocrReviewToken,
      busy: state.busy,
      finalizing: state.finalizing,
      generation: state.generation,
    };
  }

  return {
    init, open, reset, render, submit, updateField, searchRow,
    normalizeRows, payloadRows, getState,
  };
});
