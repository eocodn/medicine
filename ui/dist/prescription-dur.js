function interactionTimingHtml(timing) {
    if (!timing || timing.status !== "structured")
        return "";
    const amount = timing.amount ?? "";
    const unit = timing.unit || "시간";
    if (timing.kind === "minimum_separation") {
        return `<p>시간 조건: ${escapeHtml(amount)}${escapeHtml(unit)} 이내 병용금기</p>`;
    }
    if (timing.kind === "washout_after") {
        const subject = timing.subject ? `${escapeHtml(timing.subject)} ` : "해당 성분 ";
        return `<p>중단 후 주의기간: ${subject}종료 후 ${escapeHtml(amount)}${escapeHtml(unit)}</p>`;
    }
    return "";
}
function qualifierHtml(qualifiers) {
    const values = (qualifiers || []).filter((qualifier) => qualifier && qualifier.text);
    if (!values.length)
        return "";
    return values.map((qualifier) => {
        const label = qualifier.mode === "informational" ? "MFDS 비고" : "MFDS 적용조건";
        return `<p class="dur-qualifier"><strong>${label}</strong>: ${escapeHtml(qualifier.text)}</p>`;
    }).join("");
}
function durDetailText(item, finding = {}) {
    const title = String(finding.title || item.summary || "").trim();
    const raw = String(finding.details ?? item.details ?? "").trim();
    if (raw && raw !== "-" && raw.replace(/\s+/g, "") !== title.replace(/\s+/g, ""))
        return raw;
    if (item.category === "split_caution")
        return "분할 복용이 필요한 경우 약사에게 확인해 주세요.";
    return "DUR 데이터에 상세 설명이 제공되지 않았어요. 복용 시 주의사항은 약사 또는 처방 의료진에게 확인해 주세요.";
}
function durStatusHtml(items) {
    return [...(items || [])].sort((a, b) => Number(a.category === "split_caution") - Number(b.category === "split_caution")).map((item) => {
        const status = item.status || "unknown";
        const label = escapeHtml(item.label || item.category || "DUR 항목");
        const summary = escapeHtml(item.summary || "확인 필요");
        const findings = item.findings || [];
        if (status === "hit") {
            const detailHtml = findings.length
                ? findings.map((finding) => `
          <div class="dur-finding">
            <strong>${escapeHtml(finding.title || item.summary || "DUR 주의사항")}</strong>
            <p>${escapeHtml(durDetailText(item, finding))}</p>
            ${qualifierHtml(finding.qualifiers)}
            ${interactionTimingHtml(finding.timing)}
          </div>`).join("")
                : `<div class="dur-finding"><strong>${summary}</strong><p>${escapeHtml(durDetailText(item))}</p>${qualifierHtml(item.qualifiers)}</div>`;
            return `<section class="dur-check hit${item.category === "split_caution" ? " split-caution" : ""}">${detailHtml}</section>`;
        }
        if (status === "conditional") {
            const detailHtml = findings.length
                ? findings.map((finding) => `
          <div class="dur-finding">
            <strong>${escapeHtml(finding.title || item.summary || "DUR 주의사항")}</strong>
            <p>${escapeHtml(finding.details || item.details || "규칙의 적용 조건을 확인해야 합니다.")}</p>
            ${qualifierHtml(finding.qualifiers)}
            ${interactionTimingHtml(finding.timing)}
          </div>`).join("")
                : `<div class="dur-finding"><strong>${summary}</strong>${item.details ? `<p>${escapeHtml(item.details)}</p>` : ""}${qualifierHtml(item.qualifiers)}</div>`;
            return `<section class="dur-check conditional">${detailHtml}</section>`;
        }
        if (status === "unknown") {
            return `<section class="dur-check unknown"><div class="dur-check-heading"><strong>${label}</strong><span>${summary}</span></div>${item.details ? `<p>${escapeHtml(item.details)}</p>` : ""}${qualifierHtml(item.qualifiers)}</section>`;
        }
        const qualifier = qualifierHtml(item.qualifiers);
        return qualifier
            ? `<section class="dur-check compact ${escapeHtml(status)}"><div><strong>${label}</strong>${qualifier}</div><span>${summary}</span></section>`
            : `<div class="dur-check compact ${escapeHtml(status)}"><strong>${label}</strong><span>${summary}</span></div>`;
    }).join("");
}
function reviewItemsHtml(items) {
    return (items || []).map((item) => `
    <section class="coverage-note limited review-item">
      <strong>${escapeHtml(item.title || "추가 확인 필요")}</strong>
      ${item.details ? `<br>${escapeHtml(item.details)}` : ""}
    </section>`).join("");
}
function assessmentDetailsHtml(assessment) {
    return `${reviewItemsHtml(assessment?.review_items || [])}${durStatusHtml(assessment?.dur_checks || [])}`;
}
function hasClearDurCoverage(assessment) {
    const durChecks = assessment?.dur_checks || [];
    return durChecks.length === 7
        && durChecks.every((item) => item.status === "clear" || item.status === "not_applicable");
}
