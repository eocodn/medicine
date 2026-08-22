import { CONTEXT_TEXT, LAYOUT_FAMILIES } from "./synthetic_catalog.mjs";

export const SYNTHETIC_DOCUMENT_MODEL_VERSION = 1;

const MEDICATION_COUNT_RANGES = {
  prescription_table: [3, 7],
  compact_prescription_form: [4, 8],
  legacy_preprinted_medication_bag: [1, 1],
  classic_medication_bag: [2, 4],
  counseling_medication_bag: [3, 6],
  pharmacy_information_sheet: [6, 11],
  pharmacy_guide_receipt_sidecar: [1, 1],
};

function pick(values, random) {
  return values[Math.floor(random() * values.length)];
}

function randomInt(random, minimum, maximumInclusive) {
  return minimum + Math.floor(random() * (maximumInclusive - minimum + 1));
}

function isoDate(index, offset = 0) {
  const day = 10 + ((index + offset) % 18);
  return `2026-08-${String(day).padStart(2, "0")}`;
}

function medicationSurface(index, position, product, layoutFamily) {
  if (layoutFamily === "legacy_preprinted_medication_bag") {
    return {
      id: `med-${position}`,
      product,
      dose_text: "1포(정)",
      frequency_text: "3회",
      duration_text: "5일분",
      instruction_text: "식후 30분",
    };
  }
  if (layoutFamily === "pharmacy_guide_receipt_sidecar") {
    return {
      id: `med-${position}`,
      product,
      dose_text: position % 3 === 2 ? "0.5" : "1.00",
      frequency_text: String(1 + ((index + position) % 3)),
      duration_text: String((index + position) % 2 ? 30 : 60),
      instruction_text: position % 2 ? "아침·저녁 식후" : "식후 30분",
    };
  }
  return {
    id: `med-${position}`,
    product,
    dose_text: position % 3 === 2 ? "0.5정" : `${1 + (position % 2)}정`,
    frequency_text: `${2 + (position % 2)}회`,
    duration_text: `${3 + (position % 5)}일`,
    instruction_text: position % 2 ? "식후 30분" : "아침 저녁",
  };
}

function receiptEntries(index) {
  const amount = 4500 + (index % 4) * 1200;
  const total = amount + 12600;
  return [
    { id: "prescription-number", label: "영수증번호", value: `202608${String(1000 + index).padStart(4, "0")}` },
    { id: "dispense-date", label: "조제일자", value: isoDate(index) },
    { id: "dispense-days", label: "투약일수", value: String(index % 2 ? 30 : 60) },
    { id: "drug-cost", label: "약제비", value: total.toLocaleString("en-US") },
    { id: "insurance-cost", label: "보험자부담금", value: (total - amount).toLocaleString("en-US") },
    { id: "patient-cost", label: "본인부담금", value: amount.toLocaleString("en-US") },
    { id: "claim-count", label: "청구횟수", value: String(1 + (index % 3)) },
    { id: "pharmacy-code", label: "요양기관번호", value: String(12345670 + (index % 10)) },
  ];
}

export function buildDocumentTruth(index, random, { products, layoutFamily } = {}) {
  if (!Number.isInteger(index) || index < 0) throw new Error("document index must be a non-negative integer");
  if (typeof random !== "function") throw new Error("document random source must be a function");
  if (!Array.isArray(products) || products.length === 0) throw new Error("document products must be a non-empty array");
  if (!LAYOUT_FAMILIES.includes(layoutFamily)) throw new Error(`unsupported layout family: ${layoutFamily}`);

  const range = MEDICATION_COUNT_RANGES[layoutFamily];
  if (!range) throw new Error(`missing medication-count profile for layout family: ${layoutFamily}`);
  const span = range[1] - range[0] + 1;
  const medicationCount = range[0] + ((index + randomInt(random, 0, span - 1)) % span);
  const medications = Array.from({ length: medicationCount }, (_, position) => (
    medicationSurface(index, position, pick(products, random), layoutFamily)
  ));
  const documentType = ["prescription_table", "compact_prescription_form"].includes(layoutFamily)
    ? "prescription"
    : "medication_bag";

  return {
    schema_version: SYNTHETIC_DOCUMENT_MODEL_VERSION,
    layout_family: layoutFamily,
    document_type: documentType,
    context: {
      patient: pick(CONTEXT_TEXT.patients, random),
      clinic: pick(CONTEXT_TEXT.clinics, random),
      pharmacy: pick(CONTEXT_TEXT.pharmacies, random),
      issue_date: isoDate(index),
      dispense_date: isoDate(index, 2),
      prescription_number: `RX-${String(17000 + index).padStart(6, "0")}`,
    },
    medications,
    receipt: {
      entries: receiptEntries(index),
    },
    pictograms: ["tablet", "schedule", "caution"],
  };
}

export function medicationCountRange(layoutFamily) {
  const range = MEDICATION_COUNT_RANGES[layoutFamily];
  if (!range) throw new Error(`unsupported layout family: ${layoutFamily}`);
  return [...range];
}