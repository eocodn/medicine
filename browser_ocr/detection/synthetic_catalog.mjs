export const LAYOUT_FAMILIES = [
  "prescription_table",
  "compact_prescription_form",
  "legacy_preprinted_medication_bag",
  "classic_medication_bag",
  "counseling_medication_bag",
  "pharmacy_information_sheet",
  "pharmacy_guide_receipt_sidecar",
];

export const CAPTURE_PROFILES = [
  "flat_scan",
  "perspective_phone",
  "low_contrast_defocus",
  "glare_shadow",
  "motion_jpeg",
  "cropped_clutter",
];

export const AUGMENTATION_DIFFICULTIES = [
  "clean",
  "medium",
  "hard",
];

export const REQUIRED_AUGMENTATION_COMPONENTS = [
  "perspective",
  "defocus",
  "motion_blur",
  "jpeg_compression",
  "contrast_exposure",
  "glare",
  "shadow",
  "downscale",
  "sensor_noise",
  "white_balance",
  "partial_crop",
  "foreground_clutter",
];

export const MATERIAL_PROFILES = [
  "paper_plain",
  "paper_folded",
  "plastic_wrinkled",
];

export const PRINTER_PROFILES = [
  "laser_clean",
  "low_toner",
  "ink_bleed",
];

export const BACKGROUND_PROFILES = [
  "desk_light",
  "desk_dark",
  "pharmacy_counter",
];

export const REQUIRED_RISK_TAGS = [
  "small_text",
  "row_association",
  "column_association",
  "projective_geometry",
  "blur",
  "glare",
  "plastic_reflection",
  "partial_crop",
  "clutter",
  "jpeg_artifacts",
  "motion_blur",
  "material_fold",
  "printer_degradation",
  "downscale",
  "sensor_noise",
  "white_balance",
];

export const REQUIRED_CRITICAL_SEMANTIC_ROLES = [
  "product",
  "dose",
  "frequency",
  "duration",
];

export const CONTEXT_TEXT = {
  clinics: ["새봄의원", "한결내과", "푸른가정의학과", "온누리소아청소년과"],
  pharmacies: ["건강약국", "한마음약국", "새봄온누리약국", "우리동네약국"],
  patients: ["테스트환자", "가상환자", "홍○○", "김○○"],
  instructions: ["식후 30분", "아침 저녁", "충분한 물과 함께 복용", "졸릴 수 있으므로 주의"],
};