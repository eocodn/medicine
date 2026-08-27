(function attachMedicineOcrIntake(root, factory) {
    "use strict";
    const api = factory(root);
    if (typeof module === "object" && module.exports)
        module.exports = api;
    if (root)
        root.MedicineOcrIntake = api;
})(typeof window === "object" ? window : globalThis, function createMedicineOcrIntake(root) {
    "use strict";
    const MAX_ROWS = 24;
    const TIMEOUT_MS = 120000;
    const STRING_DRAFT_FIELDS = new Set([
        "dosage_text", "dose_unit", "meal_relation", "administration_route", "start_date", "end_date",
    ]);
    const NUMBER_DRAFT_FIELDS = new Set(["dose_amount", "frequency_per_day", "prescription_days"]);
    const ROW_FIELDS = new Set(["row_id", "product_query", "draft", "uncertainty_codes"]);
    const MEAL_RELATIONS = new Set(["unspecified", "before_meal", "after_meal", "with_meal", "empty_stomach", "regardless"]);
    const ADMINISTRATION_ROUTES = new Set(["oral", "topical", "inhaled", "ophthalmic", "otic", "nasal", "injection", "other", "unknown"]);
    const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
    let activeWorker = null;
    let timeout = null;
    let supported = false;
    function parserIssue(issues, rowIndex, field, reason, action = "dropped") {
        issues.push({ row_index: rowIndex + 1, field, reason, action });
    }
    function reportParserSanitization(issues) {
        if (!issues.length)
            return;
        // This UI boundary is intentionally tolerant: one malformed model field
        // should not throw away the rest of a usable medication row. Never log raw
        // medication values, but always surface what was discarded or rewritten so
        // parser-contract regressions remain observable during development.
        root.console?.warn?.("medicine parser output sanitized", {
            event: "parser_output_sanitized",
            issue_count: issues.length,
            issues,
        });
    }
    function isIsoDate(value) {
        if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
            return false;
        const [year, month, day] = value.split("-").map(Number);
        const parsed = new Date(Date.UTC(year, month - 1, day));
        return parsed.getUTCFullYear() === year
            && parsed.getUTCMonth() === month - 1
            && parsed.getUTCDate() === day;
    }
    function normalizeDraft(rawDraft, rowIndex, issues) {
        if (!rawDraft || typeof rawDraft !== "object" || Array.isArray(rawDraft)) {
            parserIssue(issues, rowIndex, "draft", rawDraft == null ? "missing" : "not_object");
            return {};
        }
        const draft = {};
        for (const [key, item] of Object.entries(rawDraft)) {
            if (key === "schedule_times") {
                if (item == null)
                    continue;
                if (!Array.isArray(item)) {
                    parserIssue(issues, rowIndex, key, "not_array");
                    continue;
                }
                const times = [];
                let sanitized = item.length > 24;
                for (const entry of item.slice(0, 24)) {
                    if (typeof entry !== "string" || !TIME_RE.test(entry) || times.includes(entry)) {
                        sanitized = true;
                        continue;
                    }
                    times.push(entry);
                }
                if (sanitized)
                    parserIssue(issues, rowIndex, key, "invalid_duplicate_or_excess_items");
                if (times.length)
                    draft.schedule_times = times;
            }
            else if (NUMBER_DRAFT_FIELDS.has(key)) {
                if (item == null)
                    continue;
                const valid = typeof item === "number" && Number.isFinite(item) && item > 0
                    && (key === "dose_amount" || (Number.isInteger(item) && item <= (key === "frequency_per_day" ? 24 : 3650)));
                if (valid)
                    draft[key] = item;
                else
                    parserIssue(issues, rowIndex, key, "invalid_number");
            }
            else if (key === "as_needed") {
                if (typeof item === "boolean")
                    draft.as_needed = item;
                else if (item != null)
                    parserIssue(issues, rowIndex, key, "not_boolean");
            }
            else if (STRING_DRAFT_FIELDS.has(key)) {
                const text = typeof item === "string" ? item.trim() : "";
                let valid = Boolean(text) && !/[\r\n\0]/.test(text);
                if (key === "dose_unit")
                    valid = valid && text.length <= 64;
                else
                    valid = valid && text.length <= 256;
                if (key === "meal_relation")
                    valid = valid && MEAL_RELATIONS.has(text);
                if (key === "administration_route")
                    valid = valid && ADMINISTRATION_ROUTES.has(text);
                if (key === "start_date" || key === "end_date")
                    valid = valid && isIsoDate(text);
                if (valid)
                    draft[key] = text;
                else if (item != null)
                    parserIssue(issues, rowIndex, key, "invalid_text");
            }
            else {
                parserIssue(issues, rowIndex, key, "unsupported_field");
            }
        }
        if (draft.as_needed === true) {
            for (const field of ["frequency_per_day", "schedule_times"]) {
                if (Object.hasOwn(draft, field)) {
                    delete draft[field];
                    parserIssue(issues, rowIndex, field, "conflicts_with_as_needed");
                }
            }
        }
        else if (draft.schedule_times && draft.frequency_per_day != null
            && draft.schedule_times.length !== draft.frequency_per_day) {
            delete draft.schedule_times;
            parserIssue(issues, rowIndex, "schedule_times", "frequency_mismatch");
        }
        if (draft.start_date && draft.end_date) {
            const start = new Date(`${draft.start_date}T00:00:00Z`);
            const end = new Date(`${draft.end_date}T00:00:00Z`);
            if (end < start) {
                delete draft.end_date;
                parserIssue(issues, rowIndex, "end_date", "before_start_date");
            }
            else if (draft.prescription_days != null) {
                const expected = new Date(start);
                expected.setUTCDate(expected.getUTCDate() + draft.prescription_days - 1);
                if (expected.toISOString().slice(0, 10) !== draft.end_date) {
                    delete draft.end_date;
                    parserIssue(issues, rowIndex, "end_date", "duration_mismatch");
                }
            }
        }
        return draft;
    }
    function normalizeParserRows(value) {
        const diagnostics = [];
        if (!Array.isArray(value)) {
            if (value != null)
                parserIssue(diagnostics, 0, "rows", "not_array");
            reportParserSanitization(diagnostics);
            return [];
        }
        if (value.length > MAX_ROWS)
            parserIssue(diagnostics, MAX_ROWS, "rows", "row_limit_exceeded", "truncated");
        const rows = value.slice(0, MAX_ROWS).flatMap((raw, index) => {
            if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
                parserIssue(diagnostics, index, "row", "not_object", "row_dropped");
                return [];
            }
            for (const key of Object.keys(raw)) {
                if (!ROW_FIELDS.has(key))
                    parserIssue(diagnostics, index, key, "unsupported_row_field");
            }
            if (typeof raw.product_query !== "string" || !raw.product_query.trim()) {
                parserIssue(diagnostics, index, "product_query", "missing_or_not_string", "row_dropped");
                return [];
            }
            let productQuery = raw.product_query.trim();
            if (productQuery.length > 256) {
                productQuery = productQuery.slice(0, 256);
                parserIssue(diagnostics, index, "product_query", "too_long", "truncated");
            }
            let rowId = String(raw.row_id || "");
            if (!/^[A-Za-z0-9_-]{1,64}$/.test(rowId)) {
                rowId = `parser-row-${index + 1}`;
                parserIssue(diagnostics, index, "row_id", "invalid_identifier", "rewritten");
            }
            const draft = normalizeDraft(raw.draft, index, diagnostics);
            const issueCodes = [];
            if (raw.uncertainty_codes == null) {
                parserIssue(diagnostics, index, "uncertainty_codes", "missing");
            }
            else if (!Array.isArray(raw.uncertainty_codes)) {
                parserIssue(diagnostics, index, "uncertainty_codes", "not_array");
            }
            else {
                let sanitized = raw.uncertainty_codes.length > 16;
                for (const item of raw.uncertainty_codes) {
                    if (typeof item !== "string" || !/^[A-Z][A-Z0-9_]{0,63}$/.test(item)) {
                        sanitized = true;
                        continue;
                    }
                    if (!issueCodes.includes(item) && issueCodes.length < 16)
                        issueCodes.push(item);
                }
                if (sanitized)
                    parserIssue(diagnostics, index, "uncertainty_codes", "invalid_or_excess_codes");
            }
            return [{ row_id: rowId, product_query: productQuery, draft, uncertainty_codes: issueCodes }];
        });
        reportParserSanitization(diagnostics);
        return rows;
    }
    function setStatus(message) {
        const status = root.document?.querySelector("#ocr-status");
        if (status)
            status.textContent = message || "";
    }
    function setImportDisabled(disabled, busy = false) {
        const input = root.document?.querySelector("#ocr-image-input");
        const button = root.document?.querySelector(".ocr-file-button");
        if (input)
            input.disabled = disabled;
        if (!button)
            return;
        button.classList.toggle("is-disabled", disabled);
        button.setAttribute("aria-disabled", disabled ? "true" : "false");
        if (busy)
            button.setAttribute("aria-busy", "true");
        else
            button.removeAttribute("aria-busy");
    }
    function cleanup() {
        if (timeout)
            root.clearTimeout(timeout);
        timeout = null;
        activeWorker?.terminate();
        activeWorker = null;
        setImportDisabled(!supported);
    }
    function reset() {
        cleanup();
        setStatus("");
        const input = root.document?.querySelector("#ocr-image-input");
        if (input)
            input.value = "";
    }
    function recognize(file) {
        cleanup();
        setImportDisabled(true, true);
        setStatus("사진에서 글자를 읽고 있어요… 5%");
        const worker = new Worker("/ocr-assets/direct/ocr-worker.js");
        activeWorker = worker;
        timeout = root.setTimeout(() => {
            if (activeWorker !== worker)
                return;
            cleanup();
            setStatus("인식 시간이 오래 걸려 중단했어요. 사진을 다시 선택해주세요.");
        }, TIMEOUT_MS);
        worker.onmessage = (event) => {
            if (activeWorker !== worker)
                return;
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
                }
                else if (message.parser_status === "unavailable") {
                    setStatus("문자 인식은 완료됐지만 처방전 파서 모델이 아직 준비되지 않았어요.");
                }
                else {
                    setStatus("약 정보를 찾지 못했어요.");
                }
            }
            else if (message.type === "error") {
                cleanup();
                setStatus("사진을 인식하지 못했어요. 다른 사진을 선택하거나 직접 검색해주세요.");
            }
        };
        worker.onerror = () => {
            if (activeWorker !== worker)
                return;
            cleanup();
            setStatus("사진 인식 기능을 시작하지 못했어요. 직접 검색해주세요.");
        };
        worker.postMessage({ type: "recognize", image: file });
    }
    async function runtimeAvailable() {
        if (typeof root.Worker !== "function" || typeof root.createImageBitmap !== "function"
            || typeof root.fetch !== "function")
            return false;
        try {
            const response = await root.fetch("/ocr-assets/runtime-manifest.json", { cache: "no-store" });
            if (!response.ok)
                return false;
            const manifest = await response.json();
            return manifest?.schema_version === 1
                && typeof manifest.files?.["direct/ocr-worker.js"]?.sha256 === "string";
        }
        catch (_) {
            return false;
        }
    }
    async function bind() {
        const input = root.document?.querySelector("#ocr-image-input");
        if (!input)
            return;
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
            if (file)
                recognize(file);
        });
        root.addEventListener("pagehide", reset, { once: false });
    }
    if (root.document)
        root.document.addEventListener("DOMContentLoaded", () => { void bind(); });
    return { normalizeParserRows, reset };
});
