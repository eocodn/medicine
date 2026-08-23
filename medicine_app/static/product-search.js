function productSearchResultHtml(item) {
  return `
    <article class="card result-card" data-product-select="${escapeHtml(item.product_ref)}" role="button" tabindex="0">
      <div class="result-row">
        <div class="result-copy">
          <div class="result-title-line"><strong>${escapeHtml(item.product_name)}</strong><span class="permit-badge ${escapeHtml(item.permit_status)}">${escapeHtml(permitStatusLabel(item.permit_status, item.permit_status_name))}</span></div>
          <span>${escapeHtml(item.ingredient_name || "성분 정보 없음")}${item.manufacturer ? ` · ${escapeHtml(item.manufacturer)}` : ""}</span>
          <span>${item.dur_coverage_status === "partial" ? "DUR 일부 기준 확인 필요" : item.dur_coverage_status === "complete" ? "DUR 자동 확인 가능" : "DUR 자동 확인 일부 제한"}${item.cancel_date ? ` · 허가 상태 변경일 ${escapeHtml(item.cancel_date)}` : ""}</span>
        </div>
        <span class="add-button" aria-hidden="true">추가</span>
      </div>
    </article>`;
}

function assertProductSearchPage(page) {
  if (!page || !Array.isArray(page.items) || typeof page.has_more !== "boolean") {
    throw new Error("검색 결과 형식이 올바르지 않아요");
  }
  if (page.has_more && !Number.isInteger(page.next_offset)) {
    throw new Error("검색 페이지 정보가 올바르지 않아요");
  }
  return page;
}

function renderProductSearchPage(page, { append = false } = {}) {
  const root = $("#drug-results");
  root.querySelector?.("[data-search-more]")?.remove?.();
  const cards = page.items.map(productSearchResultHtml).join("");
  if (append) root.innerHTML += cards;
  else root.innerHTML = cards || `<div class="empty-state"><strong>검색 결과가 없어요</strong>다른 제품명, 제조사 또는 성분명으로 검색해보세요.</div>`;
  if (page.has_more) {
    root.innerHTML += `<div class="search-more-sentinel" data-search-more aria-hidden="true"></div>`;
  }
}

function observeProductSearchMore(term, requestId) {
  state.searchObserver?.disconnect?.();
  state.searchObserver = null;
  if (!state.searchHasMore || typeof IntersectionObserver !== "function") return;
  const sentinel = $("#drug-results").querySelector?.("[data-search-more]");
  if (!sentinel) return;
  state.searchObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) void loadMoreProductSearch(term, requestId);
  }, { rootMargin: "240px" });
  state.searchObserver.observe(sentinel);
}

async function loadMoreProductSearch(term, requestId) {
  if (
    state.searchLoadingMore || !state.searchHasMore || state.searchNextOffset == null ||
    requestId !== state.searchRequestId || state.searchTerm !== term ||
    $("#drug-query").value.trim() !== term
  ) return false;
  const offset = state.searchNextOffset;
  state.searchLoadingMore = true;
  try {
    const page = assertProductSearchPage(await api(
      `/api/products?q=${encodeURIComponent(term)}&limit=30&offset=${offset}`,
      { coalesceKey: "product-search" },
    ));
    if (
      requestId !== state.searchRequestId || state.searchTerm !== term ||
      $("#drug-query").value.trim() !== term
    ) return false;
    renderProductSearchPage(page, { append: true });
    state.searchHasMore = page.has_more;
    state.searchNextOffset = page.next_offset;
    observeProductSearchMore(term, requestId);
    return true;
  } catch (error) {
    if (
      requestId === state.searchRequestId && state.searchTerm === term &&
      $("#drug-query").value.trim() === term
    ) $("#search-status").textContent = friendlyErrorMessage(error.message);
    return false;
  } finally {
    state.searchLoadingMore = false;
  }
}