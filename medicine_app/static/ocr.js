(function attachMedicineOcr(global) {
  "use strict";

  const SCHEMA_VERSION = 1;
  const STATES = new Set([
    "accepted", "scanner_ready", "scanning", "recognizing",
    "review_required", "cancelled", "failed", "expired",
  ]);
  const TERMINAL_STATES = new Set(["cancelled", "failed", "expired"]);
  const HINT_KEYS = new Set([
    "product_name", "ingredient_name", "product_ref", "dose_amount", "dose_unit",
    "frequency_per_day", "prescription_days", "schedule_times", "meal_relation",
    "administration_route", "as_needed", "start_date", "dose", "frequency", "days",
    "times", "dose_quantity", "duration_days",
  ]);
  const ISSUE_KEYS = new Set(["ambiguity_codes", "unsupported_codes"]);

  const state = {
    active: null,
    capabilities: null,
    initialized: false,
    onReviewRequired: null,
    onState: null,
    onClear: null,
  };

  function bridge() {
    return global.MedicineNative && typeof global.MedicineNative.postMessage === "function"
      ? global.MedicineNative : null;
  }

  function notify(message, detail) {
    if (typeof state.onState === "function") state.onState(message, detail || null);
  }

  function post(command, operationId) {
    const nativeBridge = bridge();
    if (!nativeBridge) return false;
    const message = { command, schema_version: SCHEMA_VERSION };
    if (operationId) message.operation_id = operationId;
    try {
      global.MedicineNative.postMessage(JSON.stringify(message));
      return true;
    } catch (error) {
      notify("failed", { message: "Android 브리지 요청을 보낼 수 없어요." });
      return false;
    }
  }

  function clearReviewMemory() {
    if (state.active) {
      state.active.hints = null;
      state.active.productQueries = [];
      state.active.issues = null;
      state.active.reviewToken = null;
    }
    if (typeof state.onClear === "function") state.onClear();
  }

  function scalar(value) {
    return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? value : null;
  }

  function structuredHints(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    const result = {};
    Object.entries(value).forEach(([key, item]) => {
      if (!HINT_KEYS.has(key)) return;
      if (key === "times" || key === "schedule_times") {
        if (Array.isArray(item)) result.schedule_times = item.filter((entry) => typeof entry === "string").slice(0, 24);
        return;
      }
      const safe = scalar(item);
      if (safe !== null) {
        if (key === "dose_quantity") result.dose_amount = safe;
        else if (key === "duration_days") result.prescription_days = safe;
        else result[key] = safe;
      }
    });
    return result;
  }

  function structuredIssues(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return { ambiguity_codes: [], unsupported_codes: [], messages: [] };
    const codes = (key) => ISSUE_KEYS.has(key) && Array.isArray(value[key])
      ? value[key].filter((item) => typeof item === "string").map((item) => item.trim().toUpperCase()).filter(Boolean).slice(0, 12)
      : [];
    const ambiguity = codes("ambiguity_codes");
    const unsupported = codes("unsupported_codes");
    const messages = [];
    if (ambiguity.length) messages.push("여러 약명 인식 결과가 있어 품목별 확인이 필요합니다.");
    unsupported.forEach((code) => {
      if (["UNSUPPORTED_ROUTE", "UNSUPPORTED_ADMINISTRATION_ROUTE"].includes(code)) messages.push("투여 경로를 인식하지 못해 ‘확인 필요’로 표시했습니다.");
      else if (["UNSUPPORTED_PRN", "UNSUPPORTED_AS_NEEDED", "PRN_UNSUPPORTED"].includes(code)) messages.push("필요시(PRN) 복용 여부를 인식하지 못해 직접 확인해야 합니다.");
      else messages.push(`지원되지 않는 처방 항목(${code})은 직접 확인해야 합니다.`);
    });
    return { ambiguity_codes: ambiguity, unsupported_codes: unsupported, messages };
  }

  function productQueries(value) {
    if (!Array.isArray(value)) return [];
    return value.map((item) => {
      if (typeof item === "string") return item.slice(0, 120);
      if (!item || typeof item !== "object") return null;
      const query = scalar(item.query) || scalar(item.product_name) || scalar(item.product_ref);
      return query === null ? null : String(query).slice(0, 120);
    }).filter(Boolean).slice(0, 12);
  }

  function capabilitySupported(value) {
    if (!value || typeof value !== "object") return true;
    if (value.supported === false || value.ocr === false || value.scanner === false) return false;
    return true;
  }

  function acceptEvent(input) {
    let event = input;
    if (typeof event === "string") {
      try { event = JSON.parse(event); } catch (_) { return false; }
    }
    if (!event || typeof event !== "object") return false;
    if (event.schema_version !== SCHEMA_VERSION) return false;

    if (event.capabilities && !event.state) {
      state.capabilities = event.capabilities;
      const supported = capabilitySupported(event.capabilities);
      notify(supported ? "capabilities" : "unsupported", event.capabilities);
      return true;
    }

    const loweredState = typeof event.state === "string" ? event.state.toLowerCase() : event.state;
    const eventState = loweredState === "ready" ? "review_required" : loweredState;
    if (!STATES.has(eventState) || typeof event.operation_id !== "string") return false;
    const active = state.active;
    if (!active || event.operation_id !== active.operationId) return false;
    if (!Number.isInteger(event.sequence) || event.sequence <= active.sequence) return false;
    active.sequence = event.sequence;
    active.phase = eventState;
    notify(eventState, {
      progress: typeof event.progress === "number" ? event.progress : null,
      message: scalar(event.message) || scalar(event.error_code),
    });

    if (eventState === "review_required") {
      active.hints = structuredHints(event.hints || event.structured_hints);
      active.productQueries = productQueries(event.product_queries || event.hints?.product_queries);
      active.issues = structuredIssues(event.hints || event.structured_hints);
      if (typeof state.onReviewRequired === "function") {
        state.onReviewRequired(active.hints, active.productQueries, active.operationId, active.issues);
      }
    }
    if (TERMINAL_STATES.has(eventState)) {
      clearReviewMemory();
      state.active = null;
    }
    return true;
  }

  function requestCapabilities() {
    if (!bridge()) return false;
    return post("get_capabilities");
  }

  function start() {
    if (!bridge()) {
      notify("unsupported", { message: "Android 앱에서만 처방전 스캔을 사용할 수 있어요." });
      return false;
    }
    if (state.active) {
      notify("failed", { message: "진행 중인 스캔을 먼저 취소해주세요." });
      return false;
    }
    const operationId = (global.crypto && typeof global.crypto.randomUUID === "function")
      ? global.crypto.randomUUID() : `ocr-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    state.active = {
      operationId,
      sequence: -1,
      phase: "accepted",
      hints: null,
      productQueries: [],
      issues: null,
      reviewToken: null,
    };
    if (!post("start_scan", operationId)) {
      state.active = null;
      return false;
    }
    notify("accepted", { operation_id: operationId });
    return true;
  }

  function cancel() {
    if (!state.active) return false;
    const operationId = state.active.operationId;
    clearReviewMemory();
    if (!post("cancel_scan", operationId)) return false;
    notify("scanning", { message: "스캔을 취소하는 중…" });
    return true;
  }

  function finish() {
    if (!state.active) return false;
    const operationId = state.active.operationId;
    if (!post("finish_scan", operationId)) return false;
    clearReviewMemory();
    state.active = null;
    notify("finished", { operation_id: operationId });
    return true;
  }

  function renderReview(productQueries, fallbackQuery, issues) {
    const multiple = Array.isArray(productQueries) && productQueries.length > 1;
    const query = multiple ? "" : (productQueries?.[0] || fallbackQuery || "");
    const input = global.document.querySelector("#drug-query");
    const status = global.document.querySelector("#search-status");
    if (input) input.value = query;
    const issueText = issues?.messages?.length ? ` ${issues.messages.join(" ")}` : "";
    if (status) status.textContent = multiple
      ? `여러 약명 인식 결과가 있어 품목별 확인이 필요합니다. 검색 후보를 하나씩 확인해주세요.${issueText}`
      : `처방전에서 찾은 제품 후보를 확인해주세요.${issueText}`;
    return multiple ? "" : query;
  }

  function renderState(nextState, detail) {
    const button = global.document.querySelector("#ocr-scan-button");
    const status = global.document.querySelector("#ocr-status");
    if (!button || !status) return;
    const messages = {
      accepted: "처방전 스캔을 시작했어요.", scanner_ready: "카메라를 준비했어요.", scanning: "처방전을 촬영하는 중…",
      recognizing: "처방전 내용을 기기에서 인식하는 중…", review_required: "제품을 선택하고 처방 정보를 확인해주세요.",
      cancelled: "스캔을 취소했어요.", failed: detail?.message || "스캔에 실패했어요. 다시 시도해주세요.", expired: "스캔 시간이 만료됐어요. 다시 시도해주세요.",
    };
    const progress = Number.isFinite(detail?.progress) ? ` (${Math.round(detail.progress)}%)` : "";
    status.textContent = `${messages[nextState] || detail?.message || status.textContent}${progress}`;
    if (nextState === "capabilities") {
      button.disabled = false;
      button.textContent = "처방전 사진으로 추가";
      status.textContent = "Android 스캔 기능을 사용할 수 있어요.";
    } else if (nextState === "unsupported") {
      button.disabled = true;
      status.textContent = detail?.message || "Android 앱에서만 처방전 스캔을 사용할 수 있어요.";
    } else if (["accepted", "scanner_ready", "scanning", "recognizing", "review_required"].includes(nextState)) {
      button.disabled = false;
      button.textContent = "스캔 취소";
    } else if (["cancelled", "failed", "expired", "finished"].includes(nextState)) {
      button.disabled = false;
      button.textContent = "처방전 사진으로 추가";
    }
  }

  function toggle() {
    if (state.active?.phase) cancel();
    else start();
  }

  function prefillForm(hints, issues = null) {
    if (!hints) return;
    const value = (key, fallback = null) => hints[key] ?? fallback;
    const set = (selector, next) => { const node = global.document.querySelector(selector); if (node && next !== null && next !== undefined) node.value = next; };
    set("#pending-dose-amount", value("dose_amount", value("dose")));
    set("#pending-dose-unit", value("dose_unit"));
    set("#pending-frequency", value("frequency_per_day", value("frequency")));
    set("#pending-days", value("prescription_days", value("days")));
    set("#pending-times", (value("schedule_times", value("times", [])) || []).join(", "));
    set("#pending-meal", value("meal_relation"));
    set("#pending-route", value("administration_route"));
    set("#pending-start-date", value("start_date"));
    const prn = global.document.querySelector("#pending-prn");
    if (prn && hints.as_needed !== undefined) prn.checked = Boolean(hints.as_needed);
    const unsupported = new Set(issues?.unsupported_codes || []);
    if (["UNSUPPORTED_ROUTE", "UNSUPPORTED_ADMINISTRATION_ROUTE"].some((code) => unsupported.has(code))) set("#pending-route", "unknown");
    if (["UNSUPPORTED_PRN", "UNSUPPORTED_AS_NEEDED", "PRN_UNSUPPORTED"].some((code) => unsupported.has(code)) && prn) prn.checked = false;
    const warning = global.document.querySelector("#ocr-issue-warning");
    if (warning && issues?.messages?.length) {
      warning.innerHTML = issues.messages.map((message) => `<div class="coverage-note limited ocr-issue">${String(message).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</div>`).join("");
    }
  }

  function init(options = {}) {
    state.onReviewRequired = options.onReviewRequired || null;
    state.onState = options.onState || null;
    state.onClear = options.onClear || null;
    if (state.initialized) return;
    state.initialized = true;
    global.addEventListener("message", (event) => acceptEvent(event.data));
    global.onMedicineNativeEvent = acceptEvent;
    global.__medicineOcrEvent = acceptEvent;
    if (!bridge()) {
      notify("unsupported", { message: "Android 앱에서만 처방전 스캔을 사용할 수 있어요." });
      return;
    }
    requestCapabilities();
  }

  function getReview() {
    if (!state.active || state.active.phase !== "review_required") return null;
    return {
      operation_id: state.active.operationId,
      hints: state.active.hints || {},
      issues: state.active.issues,
      review_token: state.active.reviewToken,
    };
  }

  function setReviewToken(token) {
    if (!state.active || typeof token !== "string" || !token) return false;
    state.active.reviewToken = token;
    return true;
  }

  function clearReviewToken() {
    if (!state.active) return false;
    state.active.reviewToken = null;
    return true;
  }

  global.MedicineOcr = {
    init,
    start,
    cancel,
    finish,
    toggle,
    renderState,
    renderReview,
    prefillForm,
    getReview,
    setReviewToken,
    clearReviewToken,
    handleEvent: acceptEvent,
    requestCapabilities,
    getState: () => ({
      operation_id: state.active?.operationId || null,
      sequence: state.active?.sequence ?? null,
      state: state.active?.phase || null,
    }),
  };
})(window);
