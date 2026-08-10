(function attachBrowserOcr(global) {
  "use strict";

  const SCHEMA_VERSION = 1;
  const TIMEOUT_MS = 120_000;
  const MAX_INPUT_EDGE = 1280;
  const DETECTION_INPUT_EDGE = 640;
  const WORKER_PATH = "/ocr-assets/paddle/assets/worker-entry-C9UNuyOJ.js";
  const SDK_PATH = "/ocr-assets/paddle/index.mjs";
  const DETECTION_MODEL = {
    name: "PP-OCRv5_mobile_det",
    url: "/ocr-assets/models/PP-OCRv5_mobile_det_onnx_infer.tar",
  };
  const RECOGNITION_MODEL = {
    name: "korean_PP-OCRv5_mobile_rec",
    url: "/ocr-assets/models/korean_PP-OCRv5_mobile_rec_onnx_infer.tar",
  };
  const input = global.document.querySelector("#ocr-image-input");
  let active = null;
  let epoch = 0;

  function supported() {
    return Boolean(input && global.MedicineBrowserOcrParser?.parsePrescriptionHints
      && global.Worker && global.WebAssembly && global.File && global.createImageBitmap);
  }

  function dispatch(event) {
    if (typeof global.onMedicineNativeEvent === "function") global.onMedicineNativeEvent(event);
  }

  function capability() {
    const available = supported();
    dispatch({ schema_version: SCHEMA_VERSION, capabilities: {
      supported: available,
      ocr: available,
      scanner: available,
      provider: "paddleocr-wasm-cpu",
      backend: "wasm",
      model: RECOGNITION_MODEL.name,
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

  function terminateWorker(target, reason = "") {
    const worker = target.worker;
    target.worker = null;
    if (!worker) return;
    // PaddleOCR's transport rejects outstanding requests through onerror. Signalling it
    // before terminate prevents an abandoned init/predict promise on cancellation.
    if (reason && typeof worker.onerror === "function") worker.onerror({ message: reason });
    worker.terminate();
  }

  async function disposeEngine(target) {
    if (target.disposing) return target.disposing;
    const engine = target.engine;
    target.engine = null;
    target.disposing = Promise.resolve(
      engine && typeof engine.dispose === "function" ? engine.dispose() : null,
    ).catch(() => null).finally(() => terminateWorker(target));
    return target.disposing;
  }

  function abortEngine(target) {
    if (target.disposing) {
      terminateWorker(target, "OCR operation aborted.");
      return;
    }
    const engine = target.engine;
    target.engine = null;
    // Start SDK disposal first so its pending dispose request is rejected by the
    // explicit worker error below and the SDK can finish its own cleanup path.
    const disposal = engine && typeof engine.dispose === "function" ? engine.dispose() : null;
    terminateWorker(target, "OCR operation aborted.");
    target.disposing = Promise.resolve(disposal).catch(() => null);
  }

  function finishTerminal(target, state, detail = {}) {
    if (active !== target) return false;
    epoch += 1;
    global.clearTimeout(target.timeoutId);
    stopHeartbeat(target);
    clearInput();
    emit(target, state, detail);
    active = null;
    abortEngine(target);
    return true;
  }

  async function prepareInput(file) {
    const bitmap = await global.createImageBitmap(file);
    try {
      const longestEdge = Math.max(bitmap.width, bitmap.height);
      if (longestEdge <= MAX_INPUT_EDGE) return file;
      const scale = MAX_INPUT_EDGE / longestEdge;
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));
      const canvas = global.document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Canvas 2D is unavailable");
      context.fillStyle = "#fff";
      context.fillRect(0, 0, width, height);
      context.drawImage(bitmap, 0, 0, width, height);
      try {
        return await new Promise((resolve, reject) => {
          canvas.toBlob(
            (blob) => blob ? resolve(blob) : reject(new Error("Image resize failed")),
            "image/jpeg",
            0.9,
          );
        });
      } finally {
        canvas.width = 1;
        canvas.height = 1;
      }
    } finally {
      bitmap.close();
    }
  }

  function loadPaddleSdk() {
    if (typeof global.MedicinePaddleOcrLoader === "function") return global.MedicinePaddleOcrLoader();
    return import(SDK_PATH);
  }

  function createOptions(target) {
    return {
      worker: {
        createWorker() {
          const worker = new global.Worker(WORKER_PATH, { type: "module" });
          target.worker = worker;
          return worker;
        },
      },
      textDetectionModelName: DETECTION_MODEL.name,
      textDetectionModelAsset: { url: DETECTION_MODEL.url },
      textRecognitionModelName: RECOGNITION_MODEL.name,
      textRecognitionModelAsset: { url: RECOGNITION_MODEL.url },
      textDetectionBatchSize: 1,
      textRecognitionBatchSize: 4,
      ortOptions: {
        backend: "wasm",
        wasmPaths: "/ocr-assets/ort/",
        numThreads: 1,
        simd: true,
      },
    };
  }

  async function recognize(file, target, generation) {
    let results = null;
    let recognized = "";
    let preparedInput = null;
    try {
      emit(target, "scanning");
      target.progress = 0;
      emit(target, "recognizing", { progress: target.progress });
      startHeartbeat(target, generation);

      preparedInput = await prepareInput(file);
      if (active !== target || generation !== epoch) return;

      const sdk = await loadPaddleSdk();
      if (active !== target || generation !== epoch) return;
      if (!sdk?.PaddleOCR?.create) throw new Error("PaddleOCR SDK unavailable");
      target.progress = 10;
      emit(target, "recognizing", { progress: target.progress });

      const engine = await sdk.PaddleOCR.create(createOptions(target));
      target.engine = engine;
      if (active !== target || generation !== epoch) {
        await disposeEngine(target);
        return;
      }
      target.progress = 55;
      emit(target, "recognizing", { progress: target.progress });

      results = await engine.predict(preparedInput, {
        text_det_limit_side_len: DETECTION_INPUT_EDGE,
      });
      preparedInput = null;
      if (active !== target || generation !== epoch) {
        results = null;
        await disposeEngine(target);
        return;
      }
      const items = Array.isArray(results?.[0]?.items) ? results[0].items : [];
      recognized = items
        .map((item) => typeof item?.text === "string" ? item.text : "")
        .filter(Boolean)
        .join("\n");
      const hints = global.MedicineBrowserOcrParser.parsePrescriptionHints(recognized);
      recognized = "";
      results = null;
      await disposeEngine(target);
      if (active !== target || generation !== epoch) return;

      global.clearTimeout(target.timeoutId);
      stopHeartbeat(target);
      clearInput();
      target.progress = 100;
      emit(target, "recognizing", { progress: target.progress });
      emit(target, "review_required", { hints, product_queries: hints.product_queries });
    } catch (_) {
      recognized = "";
      results = null;
      await disposeEngine(target);
      finishTerminal(target, "failed", {
        error_code: "PADDLE_OCR_FAILED",
        message: "브라우저 CPU 모델에서 처방전을 인식하지 못했어요.",
      });
    }
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
        message: "진행 중인 브라우저 OCR이 있어요.",
      });
      return;
    }
    const generation = ++epoch;
    const target = {
      operationId,
      sequence: -1,
      engine: null,
      worker: null,
      disposing: null,
      timeoutId: null,
      heartbeatId: null,
      progress: 0,
    };
    active = target;
    target.timeoutId = global.setTimeout(() => {
      if (active === target && generation === epoch) {
        finishTerminal(target, "expired", { message: "브라우저 CPU OCR 시간이 만료됐어요." });
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
    void recognize(selected, target, epoch);
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
})(window);
