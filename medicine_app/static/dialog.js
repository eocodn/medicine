let previousSheetFocus = null;

function activeSheet() {
  return [...document.querySelectorAll(".bottom-sheet")].find((node) => !node.classList.contains("hidden")) || null;
}

function sheetFocusable(sheet) {
  return [...sheet.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter((node) => !node.classList.contains("hidden"));
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
  if (event.shiftKey && document.activeElement === first) {
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
  setTimeout(() => {
    const focusable = sheetFocusable(sheet);
    if (focusable.length) focusable[0].focus();
    else {
      sheet.tabIndex = -1;
      sheet.focus();
    }
  }, 0);
}

function closeSheets() {
  document.querySelector("#sheet-backdrop").classList.add("hidden");
  document.querySelectorAll(".bottom-sheet").forEach((node) => node.classList.add("hidden"));
  document.querySelector(".app-shell").inert = false;
  document.removeEventListener("keydown", trapSheetFocus);
  if (previousSheetFocus?.focus) previousSheetFocus.focus();
  previousSheetFocus = null;
  return true;
}
