let previousSheetFocus = null;

function activeSheet() {
  return [...document.querySelectorAll(".bottom-sheet")].find((node) => !node.classList.contains("hidden")) || null;
}

function sheetFocusable(sheet) {
  return [...sheet.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter((node) => !node.classList.contains("hidden"));
}

function focusSheetContent(sheet = activeSheet()) {
  if (!sheet) return false;
  const target = sheet.querySelector("[data-sheet-focus]") || sheetFocusable(sheet)[0] || sheet;
  if (target === sheet) target.tabIndex = -1;
  target.focus({ preventScroll: true });
  return true;
}

function focusPageTitle() {
  const title = document.querySelector("#page-title");
  if (!title?.focus) return false;
  title.tabIndex = -1;
  title.focus({ preventScroll: true });
  return true;
}

function trapSheetFocus(event) {
  const sheet = activeSheet();
  if (!sheet) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeSheets();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = sheetFocusable(sheet);
  if (!focusable.length) {
    event.preventDefault();
    sheet.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!focusable.includes(document.activeElement)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function openSheet(selector) {
  previousSheetFocus = document.activeElement;
  document.querySelector("#sheet-backdrop").classList.remove("hidden");
  document.querySelectorAll(".bottom-sheet").forEach((node) => node.classList.add("hidden"));
  const sheet = document.querySelector(selector);
  sheet.classList.remove("hidden");
  document.querySelector(".app-shell").inert = true;
  document.addEventListener("keydown", trapSheetFocus);
  setTimeout(() => focusSheetContent(sheet), 0);
}

function restorePreviousSheetFocus() {
  const target = previousSheetFocus;
  if (target?.focus && target.isConnected !== false) {
    target.focus({ preventScroll: true });
    if (document.activeElement === target) return true;
  }
  return focusPageTitle();
}

function closeSheets(options = {}) {
  const closingSheet = activeSheet();
  const restoreFocus = options?.restoreFocus !== false;
  document.querySelector("#sheet-backdrop").classList.add("hidden");
  document.querySelectorAll(".bottom-sheet").forEach((node) => node.classList.add("hidden"));
  document.querySelector(".app-shell").inert = false;
  document.removeEventListener("keydown", trapSheetFocus);
  if (restoreFocus && closingSheet) restorePreviousSheetFocus();
  previousSheetFocus = null;
  if (closingSheet?.id) {
    document.dispatchEvent(new CustomEvent("medicine:sheet-closed", { detail: { id: closingSheet.id } }));
  }
  return Boolean(closingSheet);
}

function closeSheetsAfterMutation() {
  const closed = closeSheets({ restoreFocus: false });
  focusPageTitle();
  return closed;
}

function handleNativeBack() {
  if (!activeSheet()) return false;
  closeSheets();
  return true;
}

if (typeof window === "object") {
  window.MedicineDialog = { handleNativeBack };
}
