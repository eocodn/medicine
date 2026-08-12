from __future__ import annotations

# These identities were reviewed against the active MFDS product catalog, its exact
# EDI-linked DUR product ingredient text, and the ingredient-level DUR identity used
# by the current safety rules. They are intentionally explicit rather than generic
# salt/ester/spelling rules. The derivation layer still requires the alias text to be
# observed in the current DUR product rules, DUR product catalog, or an active
# exact-EDI product, and the target to exist in the current DUR ingredient index
# before any relation is admitted.
MANUALLY_REVIEWED_INGREDIENT_ALIASES: dict[str, dict[str, str]] = {
    # DUR explicitly publishes the active moiety in the product ingredient text.
    "alogliptin benzoate (as alogliptin)": {"target": "alogliptin", "basis": "explicit_active_moiety"},
    "amlodipine besylate (as amlodipine 5mg)": {"target": "amlodipine", "basis": "explicit_active_moiety"},
    "benserazide hydrochloride (as benserazide)": {"target": "benserazide", "basis": "explicit_active_moiety"},
    "empagliflozin l-proline (as empagliflozin)": {"target": "empagliflozin", "basis": "explicit_active_moiety"},
    "empagliflozin l-proline (as empagliflozin 5mg)": {"target": "empagliflozin", "basis": "explicit_active_moiety"},
    "empagliflozin l-proline (as empagliflozin 10mg)": {"target": "empagliflozin", "basis": "explicit_active_moiety"},
    "empagliflozin l-proline (as empagliflozin 12.5mg)": {"target": "empagliflozin", "basis": "explicit_active_moiety"},
    "empagliflozin l-proline (as empagliflozin 25mg)": {"target": "empagliflozin", "basis": "explicit_active_moiety"},
    "gemigliptin tartrate sesquihydrate(as gemigliptin 50mg)": {"target": "gemigliptin", "basis": "explicit_active_moiety"},
    "losartan potassium (as losartan 45.8mg)": {"target": "losartan", "basis": "explicit_active_moiety"},
    "losartan potassium (as losartan 91.6mg)": {"target": "losartan", "basis": "explicit_active_moiety"},
    "rosuvastatin calcium (as rosuvastatin 2.5mg)": {"target": "rosuvastatin", "basis": "explicit_active_moiety"},
    "s-amlodipine besylate (as s-amlodipine 5mg)": {"target": "s-amlodipine", "basis": "explicit_active_moiety"},
    "sitagliptin phosphate hydrate (as sitagliptin 0.1g)": {"target": "sitagliptin", "basis": "explicit_active_moiety"},
    "sitagliptin phosphate hydrate (as sitagliptin 50mg)": {"target": "sitagliptin", "basis": "explicit_active_moiety"},
    "timolol maleate (as timolol)": {"target": "timolol", "basis": "explicit_active_moiety"},

    # Product DUR publishes the pharmaceutical salt/form while ingredient DUR uses
    # the active-moiety identity. Each pair was reviewed individually; this is not a
    # suffix-stripping algorithm.
    "alfuzosin hydrochloride": {"target": "alfuzosin", "basis": "reviewed_salt_active_moiety"},
    "brimonidine tartrate": {"target": "brimonidine", "basis": "reviewed_salt_active_moiety"},
    "bupivacaine hydrochloride": {"target": "bupivacaine", "basis": "reviewed_salt_active_moiety"},
    "camostat mesilate": {"target": "camostat", "basis": "reviewed_salt_active_moiety"},
    "cetrorelix acetate": {"target": "cetrorelix", "basis": "reviewed_salt_active_moiety"},
    "cimetropium bromide": {"target": "cimetropium", "basis": "reviewed_salt_active_moiety"},
    "clidinium bromide": {"target": "clidinium", "basis": "reviewed_salt_active_moiety"},
    "cyclopentolate hydrochloride": {"target": "cyclopentolate", "basis": "reviewed_salt_active_moiety"},
    "ergotamine tartrate": {"target": "ergotamine", "basis": "reviewed_salt_active_moiety"},
    "fludrocortisone acetate": {"target": "fludrocortisone", "basis": "reviewed_salt_active_moiety"},
    "gabexate mesilate": {"target": "gabexate", "basis": "reviewed_salt_active_moiety"},
    "ganirelix acetate": {"target": "ganirelix", "basis": "reviewed_salt_active_moiety"},
    "lurasidone hydrochloride": {"target": "lurasidone", "basis": "reviewed_salt_active_moiety"},
    "methylprednisolone sodium succinate": {"target": "methylprednisolone", "basis": "reviewed_salt_active_moiety"},
    "nafamostat mesilate": {"target": "nafamostat", "basis": "reviewed_salt_active_moiety"},
    "nafcillin sodium": {"target": "nafcillin", "basis": "reviewed_salt_active_moiety"},
    "naltrexone hydrochloride": {"target": "naltrexone", "basis": "reviewed_salt_active_moiety"},
    "neomycin sulfate": {"target": "neomycin", "basis": "reviewed_salt_active_moiety"},
    "propafenone hydrochloride": {"target": "propafenone", "basis": "reviewed_salt_active_moiety"},
    "pyronaridine phosphate": {"target": "pyronaridine", "basis": "reviewed_salt_active_moiety"},
    "ritodrine hydrochloride": {"target": "ritodrine", "basis": "reviewed_salt_active_moiety"},
    "zastaprazan citrate": {"target": "zastaprazan", "basis": "reviewed_salt_active_moiety"},
    "butamirate citrate": {"target": "butamirate", "basis": "reviewed_salt_active_moiety"},
    "dicyclomine hydrochloride": {"target": "dicyclomine", "basis": "reviewed_salt_active_moiety"},
    "diethylpropion hydrochloride": {"target": "diethylpropion", "basis": "reviewed_salt_active_moiety"},
    "diphenhydramine hydrochloride": {"target": "diphenhydramine", "basis": "reviewed_salt_active_moiety"},
    "hydrocortisone sodium succinate": {"target": "hydrocortisone", "basis": "reviewed_salt_active_moiety"},
    "isoproterenol hydrochloride": {"target": "isoproterenol", "basis": "reviewed_salt_active_moiety"},
    "netilmicin sulfate": {"target": "netilmicin", "basis": "reviewed_salt_active_moiety"},
    "oxymetazoline hydrochloride": {"target": "oxymetazoline", "basis": "reviewed_salt_active_moiety"},
    "phendimetrazine tartrate": {"target": "phendimetrazine", "basis": "reviewed_salt_active_moiety"},
    "phentermine hydrochloride": {"target": "phentermine", "basis": "reviewed_salt_active_moiety"},
    "ulipristal acetate": {"target": "ulipristal", "basis": "reviewed_salt_active_moiety"},
    "xylometazoline hydrochloride": {"target": "xylometazoline", "basis": "reviewed_salt_active_moiety"},
    "doxylamine succinate": {"target": "doxylamine", "basis": "reviewed_salt_active_moiety"},
    "sodium tianeptine": {"target": "tianeptine", "basis": "reviewed_salt_active_moiety"},
    "clonixin lysinate": {"target": "clonixin", "basis": "reviewed_salt_active_moiety"},
    "flunarizine hcl": {"target": "flunarizine", "basis": "reviewed_salt_active_moiety"},
    "tolterodine l-tartrate": {"target": "tolterodine", "basis": "reviewed_salt_active_moiety"},
    "bendamustine hcl": {"target": "bendamustine", "basis": "reviewed_salt_active_moiety"},
    "pilsicainide hcl": {"target": "pilsicainide", "basis": "reviewed_salt_active_moiety"},
    "clonidine hcl": {"target": "clonidine", "basis": "reviewed_salt_active_moiety"},
    "peramivir hydrate": {"target": "peramivir", "basis": "reviewed_salt_active_moiety"},

    # Nomenclature / source-text variants reviewed as the same identity.
    "6β-iodomethyl-19-norcholest-5(10)-en-3β-ol(i-131)": {"target": "6β-iodomethyl-19-norcholest-5(10)-en-3β-ol(131i)", "basis": "reviewed_nomenclature_variant"},
    "clomiphen": {"target": "clomiphene", "basis": "reviewed_nomenclature_variant"},
    "human chorionic gonadotrophin": {"target": "human chorionic gonadotropin", "basis": "reviewed_nomenclature_variant"},
    "micronized progesterone": {"target": "(micronized)progesterone", "basis": "reviewed_nomenclature_variant"},
    "n(2)-l-alanyl-l-glutamine": {"target": "l-alanyl-l-glutamine", "basis": "reviewed_nomenclature_variant"},
    "perfluorobutane": {"target": "perfluoro-n-butane", "basis": "reviewed_nomenclature_variant"},
    "raloxifene": {"target": "raloxifen", "basis": "reviewed_nomenclature_variant"},
    "sodium iodide(i-131)": {"target": "sodium iodide (i-131)", "basis": "reviewed_nomenclature_variant"},
    "thallium chloride(tl-201)": {"target": "thallium chloride(201tl)", "basis": "reviewed_nomenclature_variant"},
    "cholecalciferol(as vitamin d3)": {"target": "cholecalciferol", "basis": "reviewed_active_ingredient_name"},
    "sodium alendronate(as alendronic acid)": {"target": "alendronate", "basis": "reviewed_active_ingredient_name"},
    "leuprolide": {"target": "leuprorelin = leuprolide", "basis": "reviewed_nomenclature_variant"},
    "milk thistle fruit ext.": {"target": "milk-thistle fruit dry extract(milk thistle dry extract)", "basis": "reviewed_extract_identity"},
    "pelargonium sidoides 11% ethanol ext.(1→8~10) (as dried pelargonium sidoides ethanol ext.)": {"target": "pelargonium sidoides", "basis": "reviewed_extract_identity"},
    "pelargonium sidoides 11% ethanol ext.(1→8~10)ᆞglycerin mixed solution(8:2)": {"target": "pelargonium sidoides", "basis": "reviewed_extract_identity"},
    "gemcitabine hydrochloride (as gemcitabine 1g(38mg/ml))": {"target": "gemcitabine", "basis": "explicit_active_moiety"},
    "gemcitabine hydrochloride (as gemcitabine 0.2g(38mg/ml))": {"target": "gemcitabine", "basis": "explicit_active_moiety"},
    "gemcitabine hydrochloride (as gemcitabine 2g(38mg/ml))": {"target": "gemcitabine", "basis": "explicit_active_moiety"},
    "dexamethasone disodium phosphate 5mg(5mg/ml)": {"target": "dexamethasone", "basis": "reviewed_salt_active_moiety"},
    "pamidronate disodium (as pamidronate 15mg(15mg/ml))": {"target": "pamidronate", "basis": "explicit_active_moiety"},
    "hydrocodone bitartrate": {"target": "hydrocodone", "basis": "reviewed_salt_active_moiety"},
    "roflumilast(micronised)": {"target": "roflumilast", "basis": "reviewed_formulation_identity"},
    "zoledronic acid 4mg(0.8mg/ml))": {"target": "zoledronic acid", "basis": "reviewed_strength_annotation"},
    "oxytocin 10i.u(10i.u/ml)": {"target": "oxytocin", "basis": "reviewed_strength_annotation"},
    "methylergonovine maleate 0.2mg(0.2mg/ml)": {"target": "methylergometrine(methylergonovine)", "basis": "reviewed_nomenclature_variant"},
    "vincristine sulfate 1mg(1mg/ml)": {"target": "vincristine", "basis": "reviewed_salt_active_moiety"},
    "doxorubicin hydrochloride 10mg(2mg/ml)": {"target": "doxorubicin", "basis": "reviewed_salt_active_moiety"},
    "doxorubicin hydrochloride 50mg(2mg/ml)": {"target": "doxorubicin", "basis": "reviewed_salt_active_moiety"},
    "esmolol hydrochloride 0.1g(10mg/ml)": {"target": "esmolol", "basis": "reviewed_salt_active_moiety"},
    "esmolol hydrochloride 2.5g(0.25g/ml)": {"target": "esmolol", "basis": "reviewed_salt_active_moiety"},
    "mitoxantrone hydrochloride 23.3mg(2.33mg/ml)": {"target": "mitoxantrone", "basis": "reviewed_salt_active_moiety"},
    "vinblastine sulfate 10mg(1mg/ml)": {"target": "vinblastine", "basis": "reviewed_salt_active_moiety"},
    "beclomethasone dipropionate": {"target": "beclomethasone", "basis": "reviewed_ester_active_moiety"},
    "eribulin mesylate (as eribulin 0.88mg(0.44mg/ml))": {"target": "eribulin", "basis": "explicit_active_moiety"},
    "indacaterol maleate (as indacaterol 3.3mg(0.11mg/캡슐))": {"target": "indacaterol", "basis": "explicit_active_moiety"},
    "glatiramer acetate 40mg(40mg/ml)": {"target": "glatiramer", "basis": "reviewed_salt_active_moiety"},
    "zinc histidine dihydrate": {"target": "zinc histidine", "basis": "reviewed_hydrate_identity"},
    "maribavir micronized": {"target": "maribavir", "basis": "reviewed_formulation_identity"},
    "vonoprazan tosylate": {"target": "vonoprazan", "basis": "reviewed_salt_active_moiety"},
    "interferon β-1b": {"target": "interferon beta-1b", "basis": "reviewed_nomenclature_variant"},
    "lutropin alfa": {"target": "lutropin α", "basis": "reviewed_nomenclature_variant"},
    "elosulfase α": {"target": "elosulfase alfa", "basis": "reviewed_nomenclature_variant"},
    "asfotase α": {"target": "asfotase alfa", "basis": "reviewed_nomenclature_variant"},
    "pelargonium sidoides extract": {"target": "pelargonium sidoides", "basis": "reviewed_extract_identity"},
    "milk thistle fruit ext. powder": {"target": "milk-thistle fruit dry extract(milk thistle dry extract)", "basis": "reviewed_extract_identity"},
    "interferon β-1a 22μg(44μg/ml)": {"target": "interferon beta-1a", "basis": "reviewed_strength_annotation"},
    "interferon β-1a 44μg(88μg/ml)": {"target": "interferon beta-1a", "basis": "reviewed_strength_annotation"},
    "sodium ferric gluconate complex (as fe iii)": {"target": "sodiumferricgluconatecomplex", "basis": "reviewed_nomenclature_variant"},
    "metaiodobenzylguanidine(i-123)": {"target": "3-iodobenzylguanidine(123i)", "basis": "reviewed_nomenclature_variant"},
    "phenytoin sodium 0.1g(50mg/ml)": {"target": "phenytoin", "basis": "reviewed_salt_active_moiety"},
    "follitropin-α": {"target": "follicle-stimulating hormone(follitropin, follitropin alfa, follitropin beta, corifollitropin alfa, urofollitropin)", "basis": "reviewed_class_identity"},
    "follitropin-α 300i.u(600i.u/ml)": {"target": "follicle-stimulating hormone(follitropin, follitropin alfa, follitropin beta, corifollitropin alfa, urofollitropin)", "basis": "reviewed_class_identity"},
    "follitropin-α 450i.u(600i.u/ml)": {"target": "follicle-stimulating hormone(follitropin, follitropin alfa, follitropin beta, corifollitropin alfa, urofollitropin)", "basis": "reviewed_class_identity"},
    "follitropin-α 900i.u(600i.u/ml)": {"target": "follicle-stimulating hormone(follitropin, follitropin alfa, follitropin beta, corifollitropin alfa, urofollitropin)", "basis": "reviewed_class_identity"},
}

# Intentionally not curated as one-to-one aliases. The current ingredient DUR
# contains separate rule-bearing identities for both targets, so choosing one would
# hide valid safety rules. These require multi-identity/equivalence support instead.
MANUAL_REVIEW_MULTI_IDENTITY = {
    "potassium azilsartan medoxomil": (
        "azilsartan medoxomil",
        "azilsartan medoxomil potassium",
    ),
    "azilsartan medoxomil potassium (as azilsartan medoxomil)": (
        "azilsartan medoxomil",
        "azilsartan medoxomil potassium",
    ),
    "menotrophin hp": ("menotrophin", "menotrophin hp"),
    "menotrophin h.p": ("menotrophin", "menotrophin hp"),
    "olmesartan medoxomil": ("olmesartan", "olmesartan medoxomil"),
    "olmesartan": ("olmesartan", "olmesartan medoxomil"),
    "candesartan cilexetil": ("candesartan", "candesartan cilexetil"),
    "candesartan": ("candesartan", "candesartan cilexetil"),
    "cyclosporine": ("cyclosporin", "cyclosporine"),
    "cyclosporin": ("cyclosporin", "cyclosporine"),
    "microemulsion cyclosporine": ("cyclosporin", "cyclosporine"),
    "microemulsion cyclosporine 5g(0.1g/ml)": ("cyclosporin", "cyclosporine"),
    "cyclosporine 0.25g(50mg/ml)": ("cyclosporin", "cyclosporine"),
    "azathioprine": ("azathioprin", "azathioprine"),
    "azathioprin": ("azathioprin", "azathioprine"),
    "adefovir dipivoxil": ("adefovir", "adefovir dipivoxil"),
    "adefovir": ("adefovir", "adefovir dipivoxil"),
}

# Reviewed source-data conflicts are keyed by exact DUR product code and both
# independently resolved ingredient identities. They are not generic mismatch
# rules: if a future DUR snapshot corrects the ingredient text, the tuple stops
# matching and the product automatically leaves the fail-closed path.
REVIEWED_EXACT_EDI_IDENTITY_CONFLICTS = {
    "668903311": {
        "catalog_targets": ("somatropin",),
        "dur_targets": ("somatostatin",),
        "basis": "DUR product ingredient typo; MFDS product and product name identify somatropin",
    },
}


def is_reviewed_exact_edi_identity_conflict(
    product_code: str | None,
    catalog_targets: list[str] | tuple[str, ...] | set[str],
    dur_targets: list[str] | tuple[str, ...] | set[str],
) -> bool:
    record = REVIEWED_EXACT_EDI_IDENTITY_CONFLICTS.get(str(product_code or ""))
    if record is None:
        return False
    return (
        tuple(sorted(set(catalog_targets))) == tuple(sorted(record["catalog_targets"]))
        and tuple(sorted(set(dur_targets))) == tuple(sorted(record["dur_targets"]))
    )


__all__ = [
    "MANUALLY_REVIEWED_INGREDIENT_ALIASES",
    "MANUAL_REVIEW_MULTI_IDENTITY",
    "REVIEWED_EXACT_EDI_IDENTITY_CONFLICTS",
    "is_reviewed_exact_edi_identity_conflict",
]
