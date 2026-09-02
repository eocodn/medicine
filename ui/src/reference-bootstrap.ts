type ReferenceBootstrapState =
  | "checking"
  | "download_required"
  | "downloading"
  | "installing"
  | "ready"
  | "failed"
  | "unavailable";

type ReferenceBootstrapStatus = {
  state: ReferenceBootstrapState;
  completed_bytes: number;
  total_bytes: number;
  detail?: string | null;
};

(() => {
  const pending = new Map<string, (status: ReferenceBootstrapStatus) => void>();
  let nextRequestId = 0;

  function formatBytes(bytes: number): string {
    const mib = Math.max(0, Number(bytes) || 0) / (1024 * 1024);
    return `${mib.toFixed(1)} MB`;
  }

  function normalizedStatus(value: any): ReferenceBootstrapStatus {
    const raw = value?.reference_bootstrap || value || {};
    const state = String(raw.state || "ready") as ReferenceBootstrapState;
    return {
      state,
      completed_bytes: Math.max(0, Number(raw.completed_bytes) || 0),
      total_bytes: Math.max(0, Number(raw.total_bytes) || 0),
      detail: typeof raw.detail === "string" ? raw.detail : null,
    };
  }

  function nativeRequest(action: string): Promise<ReferenceBootstrapStatus> {
    const native = window.MedicineBootstrapNative;
    if (!native) return Promise.reject(new Error("native bootstrap bridge is unavailable"));
    const requestId = `bootstrap-${++nextRequestId}`;
    return new Promise((resolve) => {
      pending.set(requestId, resolve);
      native.requestAsync(requestId, action);
    });
  }

  async function status(): Promise<ReferenceBootstrapStatus> {
    if (window.MedicineBootstrapNative) return nativeRequest("status");
    const response = await fetch("/api/development/status", { cache: "no-store" });
    if (!response.ok) throw new Error("bootstrap status request failed");
    return normalizedStatus(await response.json());
  }

  async function start(): Promise<void> {
    if (window.MedicineBootstrapNative) {
      await nativeRequest("start");
      return;
    }
    const response = await fetch("/api/development/reference-bootstrap/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok && response.status !== 202) throw new Error("bootstrap start request failed");
  }

  function closeApp(): void {
    if (window.MedicineBootstrapNative) {
      window.MedicineBootstrapNative.closeApp();
      return;
    }
    window.location.replace("about:blank");
  }

  function ensureModal(): HTMLElement {
    let modal = document.querySelector<HTMLElement>("#reference-bootstrap-modal");
    if (modal) return modal;
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
    modal.querySelector<HTMLButtonElement>("#reference-bootstrap-exit")?.addEventListener("click", closeApp);
    return modal;
  }

  function render(current: ReferenceBootstrapStatus): void {
    const modal = ensureModal();
    const message = modal.querySelector<HTMLElement>("#reference-bootstrap-message")!;
    const progress = modal.querySelector<HTMLProgressElement>("#reference-bootstrap-progress")!;
    const bytes = modal.querySelector<HTMLElement>("#reference-bootstrap-bytes")!;
    const error = modal.querySelector<HTMLElement>("#reference-bootstrap-error")!;
    const startButton = modal.querySelector<HTMLButtonElement>("#reference-bootstrap-start")!;
    const exitButton = modal.querySelector<HTMLButtonElement>("#reference-bootstrap-exit")!;
    const total = current.total_bytes;
    const completed = Math.min(current.completed_bytes, total || current.completed_bytes);
    const percent = total > 0 ? Math.round((completed / total) * 100) : 0;

    modal.classList.remove("hidden");
    progress.classList.toggle("hidden", current.state === "download_required" || current.state === "unavailable");
    bytes.classList.toggle("hidden", total <= 0);
    progress.value = percent;
    bytes.textContent = total > 0 ? `${formatBytes(completed)} / ${formatBytes(total)} · ${percent}%` : "";
    error.classList.toggle("hidden", current.state !== "failed");
    error.textContent = current.state === "failed"
      ? current.detail && (current.detail.startsWith("manifest_") || current.detail.startsWith("prepare_"))
        ? `진단 코드: ${current.detail}`
        : "안전 데이터를 준비하지 못했습니다. 다시 시도해주세요."
      : "";
    exitButton.disabled = current.state === "downloading" || current.state === "installing";

    if (current.state === "checking") {
      message.textContent = "다운로드할 안전 데이터 정보를 확인하고 있어요.";
      startButton.classList.add("hidden");
    } else if (current.state === "download_required") {
      message.textContent = `처음 사용하려면 약 안전 데이터 DB ${formatBytes(total)}를 다운로드해야 합니다. 다운로드하지 않으면 앱을 사용할 수 없습니다.`;
      startButton.textContent = "다운로드";
      startButton.disabled = false;
      startButton.classList.remove("hidden");
    } else if (current.state === "downloading") {
      message.textContent = "안전 데이터 다운로드 중…";
      startButton.disabled = true;
      startButton.classList.remove("hidden");
    } else if (current.state === "installing") {
      message.textContent = "안전 데이터 설치 및 검증 중…";
      startButton.disabled = true;
      startButton.classList.remove("hidden");
    } else if (current.state === "failed") {
      message.textContent = current.detail === "insufficient_storage"
        ? "안전 데이터를 저장할 공간이 부족합니다. 공간을 확보한 뒤 다시 시도해주세요."
        : current.detail === "network_failed"
          ? "안전 데이터를 다운로드하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해주세요."
          : current.detail?.startsWith("manifest_http")
            ? "안전 데이터 서버에서 정보를 받지 못했습니다. 잠시 후 다시 시도해주세요."
            : current.detail === "manifest_json"
              ? "안전 데이터 서버 응답을 읽지 못했습니다. 잠시 후 다시 시도해주세요."
              : current.detail === "manifest_signature"
                ? "안전 데이터 정보의 서명을 검증하지 못했습니다. 앱을 업데이트한 뒤 다시 시도해주세요."
                : current.detail === "manifest_release"
                  ? "현재 앱과 맞는 안전 데이터 정보를 해석하지 못했습니다. 앱을 업데이트해주세요."
          : current.detail?.startsWith("prepare_")
            ? "안전 데이터 준비 상태를 확인하는 중 오류가 발생했습니다. 다시 시도해주세요."
          : current.detail === "manifest_failed"
            ? "안전 데이터 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요."
            : current.detail === "download_failed"
              ? "안전 데이터 다운로드가 완료되지 않았습니다. 다시 시도해주세요."
              : current.detail === "install_failed"
                ? "안전 데이터 설치 또는 검증에 실패했습니다. 다시 시도해주세요."
                : "안전 데이터 준비에 실패했습니다. 다시 시도해주세요.";
      startButton.textContent = "다시 시도";
      startButton.disabled = false;
      startButton.classList.remove("hidden");
    } else if (current.state === "unavailable") {
      message.textContent = "현재 앱 버전에서는 안전 데이터를 사용할 수 없습니다. 앱을 업데이트해주세요.";
      startButton.classList.add("hidden");
    }
  }

  function waitForStart(): Promise<void> {
    const modal = ensureModal();
    const button = modal.querySelector<HTMLButtonElement>("#reference-bootstrap-start")!;
    return new Promise((resolve) => button.addEventListener("click", () => resolve(), { once: true }));
  }

  async function ensureReady(): Promise<void> {
    render({ state: "checking", completed_bytes: 0, total_bytes: 0 });
    for (;;) {
      let current: ReferenceBootstrapStatus;
      try {
        current = await status();
      } catch (error) {
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
        } catch (error) {
          console.error("reference bootstrap start failed", error);
          render({ ...current, state: "failed" });
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }

  function resolve(requestId: string, rawStatus: string): void {
    const resolver = pending.get(requestId);
    if (!resolver) return;
    pending.delete(requestId);
    try {
      resolver(normalizedStatus(JSON.parse(rawStatus)));
    } catch (_) {
      resolver({ state: "failed", completed_bytes: 0, total_bytes: 0 });
    }
  }

  window.MedicineBootstrapUi = { ensureReady, resolve };
})();