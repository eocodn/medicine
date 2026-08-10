(function attachBrowserOcr(global) {
  "use strict";

  const SCHEMA_VERSION = 1;
  const TIMEOUT_MS = 120_000;
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
      && global.Worker && global.WebAssembly && global.File);
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

  async function disposeEngine(target) {
    if (target.disposing) return target.disposing;
    const engine = target.engine;
    target.engine = null;
    if (!engine || typeof engine.dispose !== "function") return null;
    target.disposing = Promise.resolve(engine.dispose()).catch(() => null);
    return target.disposing;
  }

  function finishTerminal(target, state, detail = {}) {
    if (active !== target) return false;
    epoch += 1;
    global.clearTimeout(target.timeoutId);
    stopHeartbeat(target);
    clearInput();
    emit(target, state, detail);
    active = null;
    void disposeEngine(target);
    return true;
  }

  function loadPaddleSdk() {
    if (typeof global.MedicinePaddleOcrLoader === "function") return global.MedicinePaddleOcrLoader();
    return import(SDK_PATH);
  }

  function createOptions() {
    return {
      worker: true,
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
    try {
      emit(target, "scanning");
      target.progress = 0;
      emit(target, "recognizing", { progress: target.progress });
      startHeartbeat(target, generation);

      const sdk = await loadPaddleSdk();
      if (active !== target || generation !== epoch) return;
      if (!sdk?.PaddleOCR?.create) throw new Error("PaddleOCR SDK unavailable");
      target.progress = 10;
      emit(target, "recognizing", { progress: target.progress });

      const engine = await sdk.PaddleOCR.create(createOptions());
      target.engine = engine;
      if (active !== target || generation !== epoch) {
        await disposeEngine(target);
        return;
      }
      target.progress = 55;
      emit(target, "recognizing", { progress: target.progress });

      results = await engine.predict(file);
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
