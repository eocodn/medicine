(function attachMedicineOcrIntake(root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.MedicineOcrIntake = api;
})(typeof window === "object" ? window : globalThis, function createMedicineOcrIntake(root) {
  "use strict";

  const TIMEOUT_MS = 120000;
  const MAX_QUERY_COUNT = 96;
  const MAX_SPAN_NODES = 3;
  const MAX_QUERY_LENGTH = 512;
  let activeWorker = null;
  let timeout = null;
  let supported = false;

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

  function safeId(value, index) {
    const id = String(value || "").trim();
    return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : `region-${String(index + 1).padStart(4, "0")}`;
  }

  function bounds(poly) {
    if (!Array.isArray(poly) || poly.length !== 4) return null;
    const xs = [];
    const ys = [];
    for (const point of poly) {
      if (!Array.isArray(point) || point.length !== 2) return null;
      const x = Number(point[0]);
      const y = Number(point[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      xs.push(x);
      ys.push(y);
    }
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  function normalizeOcrItems(value) {
    if (!Array.isArray(value)) return [];
    return value.flatMap((raw, index) => {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
      const text = typeof raw.text === "string" ? raw.text.trim() : "";
      const box = bounds(raw.poly);
      if (!text || !box) return [];
      return [{ id: safeId(raw.id, index), text, poly: raw.poly, bounds: box }];
    });
  }

  function spatiallyContiguous(left, right) {
    const [lx1, ly1, lx2, ly2] = left.bounds;
    const [rx1, ry1, rx2, ry2] = right.bounds;
    const leftHeight = Math.max(1, ly2 - ly1);
    const rightHeight = Math.max(1, ry2 - ry1);
    const smallerHeight = Math.min(leftHeight, rightHeight);
    if (Math.max(leftHeight, rightHeight) / smallerHeight > 1.8) return false;
    const horizontalGap = Math.max(0, rx1 - lx2, lx1 - rx2);
    const verticalGap = Math.max(0, ry1 - ly2, ly1 - ry2);
    return Math.hypot(horizontalGap, verticalGap) <= 1.5 * smallerHeight;
  }

  function buildMedicationQueries(value) {
    const items = normalizeOcrItems(value);
    const queries = [];
    let serial = 0;
    for (let start = 0; start < items.length && queries.length < MAX_QUERY_COUNT; start += 1) {
      let maxLength = 1;
      while (maxLength < MAX_SPAN_NODES && start + maxLength < items.length
        && spatiallyContiguous(items[start + maxLength - 1], items[start + maxLength])) {
        maxLength += 1;
      }
      for (let length = maxLength; length >= 1 && queries.length < MAX_QUERY_COUNT; length -= 1) {
        const span = items.slice(start, start + length);
        const text = span.map((item) => item.text).join(" ").trim();
        if (!text || text.length > MAX_QUERY_LENGTH) continue;
        serial += 1;
        queries.push({
          query_id: `ocr-q-${String(serial).padStart(3, "0")}`,
          text,
          node_ids: span.map((item) => item.id),
        });
      }
    }
    return queries;
  }

  async function discoverMedicationRows(items, request) {
    const queries = buildMedicationQueries(items);
    if (!queries.length) {
      setStatus("약품 검색에 사용할 문자를 찾지 못했어요.");
      return [];
    }
    try {
      const response = await request("/api/products/ocr-candidates", {
        method: "POST",
        body: JSON.stringify({ queries }),
        coalesceKey: "ocr-product-candidates",
      });
      const rows = Array.isArray(response?.rows) ? response.rows.flatMap((row) => {
        const rowId = typeof row?.row_id === "string" ? row.row_id.trim() : "";
        const productQuery = typeof row?.product_query === "string" ? row.product_query.trim() : "";
        if (!/^[A-Za-z0-9_-]{1,64}$/.test(rowId) || !productQuery || productQuery.length > 256) return [];
        return [{ row_id: rowId, product_query: productQuery }];
      }) : [];
      setStatus(rows.length
        ? `약 ${rows.length}개를 찾았어요. 제품을 확인해주세요.`
        : "약품으로 확인되는 문자를 찾지 못했어요.");
      return rows;
    } catch (error) {
      root.console?.error?.("OCR medication candidate search failed", error);
      setStatus("약품 후보를 찾지 못했어요. 직접 검색해주세요.");
      return [];
    }
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
        const items = normalizeOcrItems(message.items);
        cleanup();
        if (items.length) {
          setStatus("문자 인식이 끝났어요. 약품 후보를 찾고 있어요.");
          root.dispatchEvent(new root.CustomEvent("medicine:ocr-result", { detail: { items } }));
        } else {
          setStatus("사진에서 검색할 문자를 찾지 못했어요.");
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

  async function runtimeAvailable() {
    if (typeof root.Worker !== "function" || typeof root.createImageBitmap !== "function"
      || typeof root.fetch !== "function") return false;
    try {
      const response = await root.fetch("/ocr-assets/runtime-manifest.json", { cache: "no-store" });
      if (!response.ok) return false;
      const manifest = await response.json();
      return manifest?.schema_version === 1
        && typeof manifest.files?.["direct/ocr-worker.js"]?.sha256 === "string";
    } catch (_) {
      return false;
    }
  }

  async function bind() {
    const input = root.document?.querySelector("#ocr-image-input");
    if (!input) return;
    setImportDisabled(true);
    supported = await runtimeAvailable();
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

  if (root.document) root.document.addEventListener("DOMContentLoaded", () => { void bind(); });
  return { buildMedicationQueries, discoverMedicationRows, normalizeOcrItems, reset, setStatus };
});
