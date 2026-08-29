(() => {
    const pending = new Map();
    let nextRequestId = 0;
    function formatBytes(bytes) {
        const mib = Math.max(0, Number(bytes) || 0) / (1024 * 1024);
        return `${mib.toFixed(1)} MB`;
    }
    function normalizedStatus(value) {
        const raw = value?.reference_bootstrap || value || {};
        const state = String(raw.state || "ready");
        return {
            state,
            completed_bytes: Math.max(0, Number(raw.completed_bytes) || 0),
            total_bytes: Math.max(0, Number(raw.total_bytes) || 0),
            detail: typeof raw.detail === "string" ? raw.detail : null,
        };
    }
    function nativeRequest(action) {
        const native = window.MedicineBootstrapNative;
        if (!native)
            return Promise.reject(new Error("native bootstrap bridge is unavailable"));
        const requestId = `bootstrap-${++nextRequestId}`;
        return new Promise((resolve) => {
            pending.set(requestId, resolve);
            native.requestAsync(requestId, action);
        });
    }
    async function status() {
        if (window.MedicineBootstrapNative)
            return nativeRequest("status");
        const response = await fetch("/api/development/status", { cache: "no-store" });
        if (!response.ok)
            throw new Error("bootstrap status request failed");
        return normalizedStatus(await response.json());
    }
    async function start() {
        if (window.MedicineBootstrapNative) {
            await nativeRequest("start");
            return;
        }
        const response = await fetch("/api/development/reference-bootstrap/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
        });
        if (!response.ok && response.status !== 202)
            throw new Error("bootstrap start request failed");
    }
    function closeApp() {
        if (window.MedicineBootstrapNative) {
            window.MedicineBootstrapNative.closeApp();
            return;
        }
        window.location.replace("about:blank");
    }
    function ensureModal() {
        let modal = document.querySelector("#reference-bootstrap-modal");
        if (modal)
            return modal;
        modal = document.createElement("section");
        modal.id = "reference-bootstrap-modal";
        modal.className = "reference-bootstrap-modal";
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-modal", "true");
        modal.setAttribute("aria-labelledby", "reference-bootstrap-title");
        modal.innerHTML = `
      <div class="reference-bootstrap-card">
        <p class="eyebrow">FIRST SETUP</p>
        <h2 id="reference-bootstrap-title">안전 데이터 준비</h2>
        <p id="reference-bootstrap-message" class="reference-bootstrap-message"></p>
        <progress id="reference-bootstrap-progress" class="reference-bootstrap-progress" max="100" value="0"></progress>
        <p id="reference-bootstrap-bytes" class="muted small"></p>
        <p id="reference-bootstrap-error" class="reference-bootstrap-error hidden"></p>
        <div class="reference-bootstrap-actions">
          <button id="reference-bootstrap-exit" class="secondary-button" type="button">앱 종료</button>
          <button id="reference-bootstrap-start" class="primary-button" type="button">다운로드</button>
        </div>
      </div>`;
        document.body.append(modal);
        modal.querySelector("#reference-bootstrap-exit")?.addEventListener("click", closeApp);
        return modal;
    }
    function render(current) {
        const modal = ensureModal();
        const message = modal.querySelector("#reference-bootstrap-message");
        const progress = modal.querySelector("#reference-bootstrap-progress");
        const bytes = modal.querySelector("#reference-bootstrap-bytes");
        const error = modal.querySelector("#reference-bootstrap-error");
        const startButton = modal.querySelector("#reference-bootstrap-start");
        const exitButton = modal.querySelector("#reference-bootstrap-exit");
        const total = current.total_bytes;
        const completed = Math.min(current.completed_bytes, total || current.completed_bytes);
        const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
        modal.classList.remove("hidden");
        progress.classList.toggle("hidden", current.state === "download_required" || current.state === "unavailable");
        bytes.classList.toggle("hidden", total <= 0);
        progress.value = percent;
        bytes.textContent = total > 0 ? `${formatBytes(completed)} / ${formatBytes(total)} · ${percent}%` : "";
        error.classList.toggle("hidden", current.state !== "failed");
        error.textContent = current.state === "failed" ? "안전 데이터를 준비하지 못했습니다. 다시 시도해주세요." : "";
        exitButton.disabled = current.state === "downloading" || current.state === "installing";
        if (current.state === "checking") {
            message.textContent = "다운로드할 안전 데이터 정보를 확인하고 있어요.";
            startButton.classList.add("hidden");
        }
        else if (current.state === "download_required") {
            message.textContent = `처음 사용하려면 약 안전 데이터 DB ${formatBytes(total)}를 다운로드해야 합니다. 다운로드하지 않으면 앱을 사용할 수 없습니다.`;
            startButton.textContent = "다운로드";
            startButton.disabled = false;
            startButton.classList.remove("hidden");
        }
        else if (current.state === "downloading") {
            message.textContent = "안전 데이터 다운로드 중…";
            startButton.disabled = true;
            startButton.classList.remove("hidden");
        }
        else if (current.state === "installing") {
            message.textContent = "안전 데이터 설치 및 검증 중…";
            startButton.disabled = true;
            startButton.classList.remove("hidden");
        }
        else if (current.state === "failed") {
            message.textContent = current.detail === "insufficient_storage"
                ? "안전 데이터를 저장할 공간이 부족합니다. 공간을 확보한 뒤 다시 시도해주세요."
                : "안전 데이터 준비에 실패했습니다. 인터넷 연결을 확인한 뒤 다시 시도해주세요.";
            startButton.textContent = "다시 시도";
            startButton.disabled = false;
            startButton.classList.remove("hidden");
        }
        else if (current.state === "unavailable") {
            message.textContent = "현재 앱 버전에서는 안전 데이터를 사용할 수 없습니다. 앱을 업데이트해주세요.";
            startButton.classList.add("hidden");
        }
    }
    function waitForStart() {
        const modal = ensureModal();
        const button = modal.querySelector("#reference-bootstrap-start");
        return new Promise((resolve) => button.addEventListener("click", () => resolve(), { once: true }));
    }
    async function ensureReady() {
        render({ state: "checking", completed_bytes: 0, total_bytes: 0 });
        for (;;) {
            let current;
            try {
                current = await status();
            }
            catch (error) {
                console.error("reference bootstrap status failed", error);
                current = { state: "failed", completed_bytes: 0, total_bytes: 0 };
            }
            if (current.state === "ready") {
                ensureModal().classList.add("hidden");
                return;
            }
            render(current);
            if (current.state === "download_required" || current.state === "failed") {
                await waitForStart();
                render({ ...current, state: "downloading" });
                try {
                    await start();
                }
                catch (error) {
                    console.error("reference bootstrap start failed", error);
                    render({ ...current, state: "failed" });
                }
            }
            await new Promise((resolve) => setTimeout(resolve, 250));
        }
    }
    function resolve(requestId, rawStatus) {
        const resolver = pending.get(requestId);
        if (!resolver)
            return;
        pending.delete(requestId);
        try {
            resolver(normalizedStatus(JSON.parse(rawStatus)));
        }
        catch (_) {
            resolver({ state: "failed", completed_bytes: 0, total_bytes: 0 });
        }
    }
    window.MedicineBootstrapUi = { ensureReady, resolve };
})();
