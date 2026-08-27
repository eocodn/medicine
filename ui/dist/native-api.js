(function () {
    const pending = new Map();
    let nextRequestId = 0;
    function mappedError(envelope, path) {
        const body = envelope?.body || null;
        const message = body?.detail || "요청을 처리하지 못했어요";
        console.error("native request failed", { path, status: envelope?.status });
        const displayMessage = typeof window.friendlyErrorMessage === "function"
            ? window.friendlyErrorMessage(message)
            : "요청을 처리하지 못했어요";
        const error = new Error(typeof displayMessage === "string" ? displayMessage : "요청을 처리하지 못했어요");
        error.status = envelope?.status;
        error.body = body;
        error.code = body?.code || null;
        return error;
    }
    function request(path, options = {}) {
        if (!window.MedicineNative || typeof window.MedicineNative.requestAsync !== "function")
            return undefined;
        const method = String(options.method || "GET").toUpperCase();
        const requestId = `native-${++nextRequestId}`;
        return new Promise((resolve, reject) => {
            pending.set(requestId, { path, resolve, reject });
            try {
                window.MedicineNative.requestAsync(requestId, method, path, options.body || "", options.coalesceKey || "");
            }
            catch (_) {
                pending.delete(requestId);
                reject(new Error("기기 내 데이터 요청을 처리하지 못했어요"));
            }
        });
    }
    function resolve(requestId, rawEnvelope) {
        const entry = pending.get(requestId);
        if (!entry)
            return;
        pending.delete(requestId);
        let envelope;
        try {
            envelope = JSON.parse(rawEnvelope);
        }
        catch (_) {
            entry.reject(new Error("기기 내 데이터 요청을 처리하지 못했어요"));
            return;
        }
        if (!(envelope.status >= 200 && envelope.status < 300)) {
            entry.reject(mappedError(envelope, entry.path));
            return;
        }
        entry.resolve(envelope.status === 204 ? null : envelope.body);
    }
    window.MedicineLocalApi = { request, resolve };
})();
