(function attachBrowserOcr(global) {
  "use strict";

  const SCHEMA_VERSION = 1;
  const TIMEOUT_MS = 90_000;
  const PROGRESS_STAGES = {
    "loading tesseract core": [0, 10],
    "initializing tesseract": [10, 10],
    "loading language traineddata": [20, 30],
    "initializing api": [50, 10],
    "recognizing text": [60, 40],
  };
  const input = global.document.querySelector("#ocr-image-input");
  let active = null;
  let epoch = 0;

  function supported() {
    return Boolean(input && global.Tesseract?.createWorker && global.MedicineBrowserOcrParser?.parsePrescriptionHints
      && global.Worker && global.WebAssembly && global.File);
  }

  function dispatch(event) {
    if (typeof global.onMedicineNativeEvent === "function") global.onMedicineNativeEvent(event);
  }

  function capability() {
    dispatch({ schema_version: SCHEMA_VERSION, capabilities: {
      supported: supported(), ocr: supported(), scanner: supported(), provider: "browser-wasm",
    } });
  }

  function emit(state, detail = {}) {
    if (!active) return;
    active.sequence += 1;
    dispatch({
      schema_version: SCHEMA_VERSION,
      operation_id: active.operationId,
      sequence: active.sequence,
      state,
      ...detail,
    });
  }

  function clearInput() {
    if (input) input.value = "";
  }

  async function terminateWorker(target) {
    const worker = target?.worker;
    target.worker = null;
    if (worker) {
      try { await worker.terminate(); } catch (_) { /* Worker teardown is best-effort after terminal state. */ }
    }
  }

  async function stop(state, message) {
    const target = active;
    if (!target) return;
    epoch += 1;
    global.clearTimeout(target.timeoutId);
    clearInput();
    await terminateWorker(target);
    if (active !== target) return;
    emit(state, message ? { message } : {});
    active = null;
  }

  function logger(target, generation, update) {
    if (active !== target || generation !== epoch || !PROGRESS_STAGES[update?.status]) return;
    const [base, span] = PROGRESS_STAGES[update.status];
    const fraction = Number.isFinite(update.progress) ? Math.min(1, Math.max(0, update.progress)) : 0;
    const progress = Math.round(base + (fraction * span));
    emit("recognizing", { progress });
  }

  async function recognize(file, target, generation) {
    let result = null;
    let recognized = "";
    try {
      emit("scanning");
      emit("recognizing", { progress: 0 });
      target.worker = await global.Tesseract.createWorker(["kor", "eng"], 1, {
        workerPath: "/ocr-assets/worker.min.js",
        langPath: "/ocr-assets/lang",
        corePath: "/ocr-assets/core",
        logger: (update) => logger(target, generation, update),
      });
      if (active !== target || generation !== epoch) {
        await terminateWorker(target);
        return;
      }
      result = await target.worker.recognize(file);
      if (active !== target || generation !== epoch) {
        await terminateWorker(target);
        return;
      }
      recognized = typeof result?.data?.text === "string" ? result.data.text : "";
      const hints = global.MedicineBrowserOcrParser.parsePrescriptionHints(recognized);
      recognized = "";
      if (result?.data) result.data.text = "";
      result = null;
      await terminateWorker(target);
      global.clearTimeout(target.timeoutId);
      clearInput();
      if (active !== target || generation !== epoch) return;
      emit("review_required", { hints, product_queries: hints.product_queries });
    } catch (_) {
      recognized = "";
      if (result?.data) result.data.text = "";
      result = null;
      await terminateWorker(target);
      if (active === target && generation === epoch) await stop("failed", "브라우저에서 처방전을 인식하지 못했어요.");
    }
  }

  function start(operationId) {
    if (!supported()) return capability();
    if (active) {
      dispatch({
        schema_version: SCHEMA_VERSION,
        operation_id: operationId,
        sequence: 0,
        state: "failed",
        error_code: "OCR_BUSY",
        message: "진행 중인 브라우저 OCR이 있어요.",
      });
      return;
    }
    const generation = ++epoch;
    active = { operationId, sequence: -1, worker: null, timeoutId: null };
    active.timeoutId = global.setTimeout(() => {
      if (active?.operationId === operationId && generation === epoch) void stop("expired", "브라우저 OCR 시간이 만료됐어요.");
    }, TIMEOUT_MS);
    emit("accepted");
    emit("scanner_ready");
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
      void stop("cancelled");
      return;
    }
    void recognize(selected, target, epoch);
  });
  input?.addEventListener("cancel", () => { if (active) void stop("cancelled"); });

  function postMessage(serialized) {
    let command;
    try { command = JSON.parse(serialized); } catch (_) { return; }
    if (command?.schema_version !== SCHEMA_VERSION) return;
    if (command.command === "get_capabilities") capability();
    else if (command.command === "start_scan" && typeof command.operation_id === "string") start(command.operation_id);
    else if (command.command === "cancel_scan" && active?.operationId === command.operation_id) void stop("cancelled");
    else if (command.command === "finish_scan" && active?.operationId === command.operation_id) void stop("cancelled");
  }

  global.MedicineBrowserOcr = { postMessage, isSupported: supported };
})(window);
