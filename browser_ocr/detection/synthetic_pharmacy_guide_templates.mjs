const TEMPLATE_DATA = [
  {
    id: "yellow_integrated",
    accent: "#d1a91f", accentSoft: "#f4e8a7", secondary: "#9c7d17",
    paper: "#f7f4ea", receipt: "#f2efe5", border: "#d1c79a", headerText: "#2b2a23",
    receiptX: 995, rowStart: 330, rowGap: 108, receiptMode: "integrated", lowerPanel: "warning", zebra: false, stamp: false,
  },
  {
    id: "blue_striped",
    accent: "#23436f", accentSoft: "#d9e6ef", secondary: "#7ca4bf",
    paper: "#f7f8f7", receipt: "#f4f6f7", border: "#8798ab", headerText: "#f7f9fb",
    receiptX: 990, rowStart: 350, rowGap: 112, receiptMode: "integrated", lowerPanel: "schedule", zebra: true, stamp: false,
  },
  {
    id: "cream_dense_receipt",
    accent: "#c0aa4f", accentSoft: "#eee7c9", secondary: "#9b8741",
    paper: "#f4f0e5", receipt: "#efebdf", border: "#aaa38c", headerText: "#2b2a23",
    receiptX: 985, rowStart: 335, rowGap: 104, receiptMode: "integrated", lowerPanel: "warning", zebra: false, stamp: false,
  },
  {
    id: "navy_dense_guide",
    accent: "#18345f", accentSoft: "#dbe2eb", secondary: "#58779c",
    paper: "#f4f4f1", receipt: "#f1f1ee", border: "#718099", headerText: "#f7f9fb",
    receiptX: 1005, rowStart: 325, rowGap: 102, receiptMode: "detached", lowerPanel: "warning", zebra: false, stamp: true,
  },
  {
    id: "low_contrast_blue",
    accent: "#6b86a2", accentSoft: "#e3e8eb", secondary: "#9eb0bf",
    paper: "#f3f2ed", receipt: "#eef0ef", border: "#9ea9b0", headerText: "#263747",
    receiptX: 995, rowStart: 345, rowGap: 118, receiptMode: "integrated", lowerPanel: "schedule", zebra: true, stamp: false,
  },
  {
    id: "teal_modern_grid",
    accent: "#178b8d", accentSoft: "#d6eceb", secondary: "#263d5c",
    paper: "#f7f7f3", receipt: "#f3f5f4", border: "#79a8a8", headerText: "#f8fbfb",
    receiptHeaderText: "#f8fbfb",
    receiptX: 940, rowStart: 330, rowGap: 101, receiptMode: "integrated", lowerPanel: "storage", zebra: false, stamp: false,
  },
  {
    id: "pediatric_pastel",
    accent: "#69aaa8", accentSoft: "#dceeed", secondary: "#cf8f8c",
    paper: "#faf6f0", receipt: "#f4f4ed", border: "#93b6b3", headerText: "#314a49",
    receiptX: 990, rowStart: 340, rowGap: 110, receiptMode: "integrated", lowerPanel: "pediatric", zebra: false, stamp: false,
  },
  {
    id: "monochrome_stapled",
    accent: "#5c5c5a", accentSoft: "#e6e5e0", secondary: "#8c8a84",
    paper: "#f2f0ea", receipt: "#eeeae1", border: "#77746e", headerText: "#f7f7f4",
    receiptX: 982, rowStart: 350, rowGap: 112, receiptMode: "stapled", lowerPanel: "monochrome", zebra: false, stamp: true,
  },
  {
    id: "lavender_dense",
    accent: "#8a79a9", accentSoft: "#e5deed", secondary: "#65577d",
    paper: "#f7f4f1", receipt: "#f1efec", border: "#9b90ab", headerText: "#302840",
    receiptX: 1000, rowStart: 322, rowGap: 96, receiptMode: "detached", lowerPanel: "triple_info", zebra: false, stamp: false,
  },
  {
    id: "yellow_blue_split",
    accent: "#244c78", accentSoft: "#f0dc74", secondary: "#d0a72c",
    paper: "#f7f5ed", receipt: "#f1f2ed", border: "#8594a4", headerText: "#f7f9fb",
    titleText: "#f7f9fb",
    receiptX: 995, rowStart: 335, rowGap: 106, receiptMode: "integrated", lowerPanel: "compact_schedule", zebra: true, stamp: false,
  },
];

export const PHARMACY_GUIDE_TEMPLATES = Object.freeze(TEMPLATE_DATA.map((template) => Object.freeze(template)));
export const PHARMACY_GUIDE_STYLE_IDS = Object.freeze(PHARMACY_GUIDE_TEMPLATES.map((template) => template.id));

export function pharmacyGuideStyleForIndex(index) {
  if (!Number.isInteger(index) || index < 0) throw new Error("pharmacy-guide style index must be a non-negative integer");
  return PHARMACY_GUIDE_TEMPLATES[index % PHARMACY_GUIDE_TEMPLATES.length];
}