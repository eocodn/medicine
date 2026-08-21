(function attachMedicineOcrIntake(root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MedicineOcrIntake = api;
})(typeof window === "object" ? window : globalThis, function createMedicineOcrIntake(root) {
  "use strict";

  const MAX_ROWS = 24;
  const TIMEOUT_MS = 120000;
  const STRING_DRAFT_FIELDS = new Set([
    "dosage_text", "dose_unit", "meal_relation", "administration_route", "start_date", "end_date",
  ]);
  const NUMBER_DRAFT_FIELDS = new Set(["dose_amount", "frequency_per_day", "prescription_days"]);
  let activeWorker = null;
  let timeout = null;
  let supported = false;

  function normalizeParserRows(value) {
    if (!Array.isArray(value)) return [];
    return value.slice(0, MAX_ROWS).flatMap((raw, index) => {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
      const productQuery = String(raw.product_query || "").trim().slice(0, 256);
      if (!productQuery) return [];
      const draft = {};
      if (raw.draft && typeof raw.draft === "object" && !Array.isArray(raw.draft)) {
        for (const [key, item] of Object.entries(raw.draft)) {
          if (key === "schedule_times") {
            if (Array.isArray(item)) {
              const times = item.filter((entry) => typeof entry === "string" && entry.trim()).map((entry) => entry.trim()).slice(0, 24);
              if (times.length) draft.schedule_times = times;
            }
          } else if (NUMBER_DRAFT_FIELDS.has(key)) {
            const number = Number(item);
            if (Number.isFinite(number) && number > 0) draft[key] = number;
          } else if (key === "as_needed") {
            if (item === true) draft.as_needed = true;
          } else if (STRING_DRAFT_FIELDS.has(key) && typeof item === "string" && item.trim()) {
            draft[key] = item.trim().slice(0, 256);
          }
        }
      }
      const issues = Array.isArray(raw.uncertainty_codes)
        ? [...new Set(raw.uncertainty_codes.filter((item) => typeof item === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(item)))].slice(0, 16)
        : [];
      return [{
        row_id: /^[A-Za-z0-9_-]{1,64}$/.test(String(raw.row_id || "")) ? String(raw.row_id) : `parser-row-${index + 1}`,
        product_query: productQuery,
        draft,
        uncertainty_codes: issues,
      }];
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
    setStatus("");
    const input = root.document?.querySelector("#ocr-image-input");
    if (input) input.value = "";
  }

  function recognize(file) {
    cleanup();
    setImportDisabled(true, true);
    setStatus("사진에서 글자를 읽고 있어요… 5%");
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
        setStatus(`사진에서 글자를 읽고 있어요… ${Math.max(0, Math.min(100, Number(message.progress) || 0))}%`);
        return;
      }
      if (message.type === "result") {
        const rows = normalizeParserRows(message.rows);
        cleanup();
        if (rows.length) {
          setStatus(`약 ${rows.length}개를 인식했어요. 제품 후보를 찾고 있어요.`);
          root.dispatchEvent(new root.CustomEvent("medicine:parser-result", { detail: { rows } }));
        } else if (message.parser_status === "unavailable") {
          setStatus("문자 인식은 완료됐지만 처방전 파서 모델이 아직 준비되지 않았어요.");
        } else {
          setStatus("약 정보를 찾지 못했어요.");
        }
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
  return { normalizeParserRows, reset };
});
