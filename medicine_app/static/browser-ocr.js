(function attachBrowserOcr(global) {
  "use strict";

  const SCHEMA_VERSION = 1;
  const TIMEOUT_MS = 120_000;
  const WORKER_PATH = "/ocr-assets/direct/ocr-worker.js";
  const input = global.document.querySelector("#ocr-image-input");
  let active = null;
  let epoch = 0;

  function supported() {
    return Boolean(input && global.MedicineBrowserOcrParser?.parsePrescriptionDocument
      && global.Worker && global.WebAssembly && global.File);
  }

  function dispatch(event) {
    if (typeof global.onMedicineOcrEvent === "function") global.onMedicineOcrEvent(event);
  }

  function capability() {
    const available = supported();
    dispatch({ schema_version: SCHEMA_VERSION, capabilities: {
      supported: available,
      ocr: available,
      scanner: available,
      provider: "direct-onnx-wasm-cpu",
      backend: "wasm",
      model: "korean_PP-OCRv5_mobile_rec",
    } });
  }

  function emit(target, state, detail = {}) {
    if (active !== target) return;
    target.sequence += 1;
    dispatch({
      schema_version: SCHEMA_VERSION,
      operation_id: target.operationId,
      sequence: target.sequence,
      state,
      ...detail,
    });
  }

  function clearInput() {
    if (input) input.value = "";
  }

  function stopHeartbeat(target) {
    if (target.heartbeatId !== null) global.clearInterval(target.heartbeatId);
    target.heartbeatId = null;
  }

  function startHeartbeat(target, generation) {
    stopHeartbeat(target);
    target.heartbeatId = global.setInterval(() => {
      if (active !== target || generation !== epoch) return;
      target.progress = Math.min(94, target.progress + 1);
      emit(target, "recognizing", { progress: target.progress });
    }, 2_000);
  }

  function terminateWorker(target) {
    const worker = target.worker;
    target.worker = null;
    if (worker) worker.terminate();
  }

  function finishTerminal(target, state, detail = {}) {
    if (active !== target) return false;
    epoch += 1;
    global.clearTimeout(target.timeoutId);
    stopHeartbeat(target);
    terminateWorker(target);
    clearInput();
    emit(target, state, detail);
    active = null;
    return true;
  }

  function finishSuccess(target, items) {
    if (active !== target) return;
    const document = global.MedicineBrowserOcrParser.parsePrescriptionDocument(items);
    const rows = Array.isArray(document.rows) ? document.rows : [];
    const hints = rows.length === 1
      ? { ...rows[0], ambiguity_codes: document.ambiguity_codes || [], unsupported_codes: document.unsupported_codes || [] }
      : { ambiguity_codes: document.ambiguity_codes || [], unsupported_codes: document.unsupported_codes || [] };
    global.clearTimeout(target.timeoutId);
    stopHeartbeat(target);
    terminateWorker(target);
    clearInput();
    target.progress = 100;
    emit(target, "recognizing", { progress: target.progress });
    emit(target, "review_required", {
      hints,
      rows,
      product_queries: document.product_queries || [],
      ambiguity_codes: document.ambiguity_codes || [],
      unsupported_codes: document.unsupported_codes || [],
    });
    active = null;
  }

  function recognize(file, target, generation) {
    emit(target, "scanning");
    target.progress = 0;
    emit(target, "recognizing", { progress: target.progress });
    startHeartbeat(target, generation);

    const worker = new global.Worker(WORKER_PATH, { type: "classic" });
    target.worker = worker;
    worker.onmessage = (event) => {
      if (active !== target || generation !== epoch || target.worker !== worker) return;
      const message = event.data || {};
      if (message.type === "progress" && Number.isFinite(message.progress)) {
        target.progress = Math.max(target.progress, Math.min(94, message.progress));
        emit(target, "recognizing", { progress: target.progress });
      } else if (message.type === "result" && Array.isArray(message.items)) {
        finishSuccess(target, message.items);
      } else if (message.type === "error") {
        finishTerminal(target, "failed", {
          error_code: "DIRECT_ONNX_OCR_FAILED",
          message: "사진에서 처방 정보를 인식하지 못했어요. 다른 사진으로 다시 시도해주세요.",
        });
      }
    };
    worker.onerror = () => {
      finishTerminal(target, "failed", {
        error_code: "DIRECT_ONNX_OCR_FAILED",
        message: "사진에서 처방 정보를 인식하지 못했어요. 다른 사진으로 다시 시도해주세요.",
      });
    };
    worker.postMessage({ type: "recognize", image: file });
  }

  function start(operationId) {
    if (!supported()) {
      capability();
      return;
    }
    if (active) {
      dispatch({
        schema_version: SCHEMA_VERSION,
        operation_id: operationId,
        sequence: 0,
        state: "failed",
        error_code: "OCR_BUSY",
        message: "사진 인식이 이미 진행 중이에요.",
      });
      return;
    }
    const generation = ++epoch;
    const target = {
      operationId,
      sequence: -1,
      worker: null,
      timeoutId: null,
      heartbeatId: null,
      progress: 0,
    };
    active = target;
    target.timeoutId = global.setTimeout(() => {
      if (active === target && generation === epoch) {
        finishTerminal(target, "expired", { message: "사진 인식 시간이 오래 걸렸어요. 다시 시도해주세요." });
      }
    }, TIMEOUT_MS);
    emit(target, "accepted");
    emit(target, "scanner_ready");
    clearInput();
    input.click();
  }

  input?.addEventListener("change", () => {
    const target = active;
    const selected = input.files?.[0] || null;
    if (!target) {
      clearInput();
      return;
    }
    if (!selected) {
      finishTerminal(target, "cancelled");
      return;
    }
    recognize(selected, target, epoch);
  });
  input?.addEventListener("cancel", () => {
    if (active) finishTerminal(active, "cancelled");
  });

  function postMessage(serialized) {
    let command;
    try { command = JSON.parse(serialized); } catch (_) { return; }
    if (command?.schema_version !== SCHEMA_VERSION) return;
    if (command.command === "get_capabilities") capability();
    else if (command.command === "start_scan" && typeof command.operation_id === "string") start(command.operation_id);
    else if (command.command === "cancel_scan" && active?.operationId === command.operation_id) {
      finishTerminal(active, "cancelled");
    } else if (command.command === "finish_scan" && active?.operationId === command.operation_id) {
      finishTerminal(active, "cancelled");
    }
  }

  global.MedicineBrowserOcr = { postMessage, isSupported: supported };
  capability();
})(typeof window !== "undefined" ? window : globalThis);
