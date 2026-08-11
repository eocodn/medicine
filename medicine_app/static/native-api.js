(function () {
  function request(path, options = {}) {
    if (!window.MedicineNative || typeof window.MedicineNative.request !== "function") return undefined;
    const method = String(options.method || "GET").toUpperCase();
    let envelope;
    try {
      envelope = JSON.parse(window.MedicineNative.request(method, path, options.body || ""));
    } catch (_) {
      throw new Error("기기 내 데이터 요청을 처리하지 못했어요");
    }
    if (!(envelope.status >= 200 && envelope.status < 300)) {
      const body = envelope.body || null;
      const message = body?.detail || "요청을 처리하지 못했어요";
      console.error("native request failed", { path, status: envelope.status });
      const displayMessage = typeof window.friendlyErrorMessage === "function"
        ? window.friendlyErrorMessage(message)
        : message;
      const error = new Error(typeof displayMessage === "string" ? displayMessage : "요청을 처리하지 못했어요");
      error.status = envelope.status;
      error.body = body;
      throw error;
    }
    return envelope.status === 204 ? null : envelope.body;
  }

  window.MedicineLocalApi = { request };
})();
