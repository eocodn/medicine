#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import tomllib
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
GRADLE_LOCK = ROOT / "android/app/gradle.lockfile"
CARGO_LOCK = ROOT / "rust/medicine_core/Cargo.lock"
DEFAULT_OUTPUT = ROOT / "android/app/src/main/assets/THIRD_PARTY_NOTICES.txt"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def android_runtime_coordinates() -> list[str]:
    coordinates = []
    for line in GRADLE_LOCK.read_text(encoding="utf-8").splitlines():
        if "releaseRuntimeClasspath" not in line or line.startswith(("#", "empty=")):
            continue
        coordinates.append(line.split("=", 1)[0])
    return sorted(set(coordinates))


def pom_licenses(pom: Path) -> list[str]:
    root = ElementTree.parse(pom).getroot()
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    names = []
    for license_node in root.findall(".//m:licenses/m:license", namespace):
        name = (license_node.findtext("m:name", default="", namespaces=namespace) or "").strip()
        if name:
            names.append(name)
    return names


def archive_notice_texts(artifact: Path) -> list[tuple[str, str]]:
    results = []
    try:
        with zipfile.ZipFile(artifact) as archive:
            for name in sorted(archive.namelist()):
                basename = Path(name).name.upper()
                if not basename.startswith(("LICENSE", "NOTICE", "COPYING")):
                    continue
                payload = archive.read(name)
                try:
                    text = payload.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                if text:
                    results.append((name, text))
    except zipfile.BadZipFile:
        pass
    return results


def find_gradle_artifacts(cache: Path, coordinate: str) -> tuple[Path, list[Path]]:
    group, artifact, version = coordinate.split(":", 2)
    base = cache / group / artifact / version
    poms = sorted(base.glob("*/*.pom"))
    binaries = sorted(path for path in base.glob("*/*") if path.suffix in {".aar", ".jar"})
    if not poms:
        raise RuntimeError(f"missing cached POM for {coordinate}: {base}")
    return poms[0], binaries


def find_crate_source(registry_src: Path, name: str, version: str) -> Path:
    matches = sorted(registry_src.glob(f"*/{name}-{version}"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one cached crate source for {name} {version}, found {len(matches)}")
    return matches[0]


def crate_notice_files(crate_dir: Path) -> list[Path]:
    results = []
    for path in sorted(crate_dir.iterdir()):
        upper = path.name.upper()
        if path.is_file() and (upper.startswith(("LICENSE", "NOTICE", "COPYING")) or upper == "UNLICENSE"):
            results.append(path)
    return results


def add_text(
    texts: dict[str, str],
    sources: dict[str, list[str]],
    source: str,
    text: str,
) -> str:
    normalized = text.strip() + "\n"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    texts.setdefault(digest, normalized)
    sources[digest].append(source)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate notices for the no-OCR Android distribution")
    parser.add_argument("--cargo-registry-src", type=Path, required=True)
    parser.add_argument("--gradle-module-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    texts: dict[str, str] = {}
    text_sources: dict[str, list[str]] = defaultdict(list)
    android_rows: list[tuple[str, str]] = []
    apache_digest: str | None = None

    for coordinate in android_runtime_coordinates():
        pom, artifacts = find_gradle_artifacts(args.gradle_module_cache, coordinate)
        licenses = pom_licenses(pom)
        if coordinate == "com.google.guava:listenablefuture:1.0" and not licenses:
            licenses = ["Apache License, Version 2.0"]
        if not licenses or any("Apache" not in name or "2.0" not in name for name in licenses):
            raise RuntimeError(f"unreviewed Android runtime license for {coordinate}: {licenses!r}")
        android_rows.append((coordinate, "Apache-2.0"))
        for artifact in artifacts:
            for entry, text in archive_notice_texts(artifact):
                digest = add_text(texts, text_sources, f"{coordinate} :: {entry}", text)
                if "Apache License" in text and "Version 2.0" in text:
                    apache_digest = apache_digest or digest

    if apache_digest is None:
        raise RuntimeError("could not locate a reviewed Apache-2.0 license text in Android runtime artifacts")

    cargo_document = tomllib.loads(CARGO_LOCK.read_text(encoding="utf-8"))
    rust_rows: list[tuple[str, str, str]] = []
    for package in sorted(cargo_document["package"], key=lambda item: (item["name"], item["version"])):
        if package["name"] == "medicine_core":
            continue
        crate_dir = find_crate_source(args.cargo_registry_src, package["name"], package["version"])
        manifest = tomllib.loads((crate_dir / "Cargo.toml").read_text(encoding="utf-8"))
        package_metadata = manifest.get("package", {})
        license_expression = str(package_metadata.get("license") or "").strip()
        authors = ", ".join(package_metadata.get("authors") or [])
        files = crate_notice_files(crate_dir)
        if not files:
            if not license_expression or not all(token in license_expression for token in ("MIT", "Apache-2.0")):
                raise RuntimeError(
                    f"crate {package['name']} {package['version']} has no root license file and unsupported fallback: "
                    f"{license_expression!r}"
                )
            fallback = (
                f"Upstream package: {package['name']} {package['version']}\n"
                f"Authors: {authors or 'not listed in package metadata'}\n"
                f"Declared license: {license_expression}\n\n"
                "The upstream crate archive does not contain a root license file. Its package metadata offers the "
                "work under MIT and Apache-2.0; the full Apache-2.0 terms are reproduced elsewhere in this notice.\n"
            )
            digest = add_text(
                texts,
                text_sources,
                f"{package['name']} {package['version']} :: package metadata fallback",
                fallback,
            )
            rust_rows.append((package["name"], package["version"], f"{license_expression}; notice {digest[:12]}"))
            continue

        digests = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            digests.append(
                add_text(
                    texts,
                    text_sources,
                    f"{package['name']} {package['version']} :: {path.name}",
                    text,
                )
            )
        rust_rows.append(
            (
                package["name"],
                package["version"],
                f"{license_expression or 'see bundled upstream files'}; notices "
                + ", ".join(digest[:12] for digest in digests),
            )
        )

    lines = [
        "THIRD-PARTY NOTICES FOR YAKBOM ANDROID",
        "=======================================",
        "",
        "This file accompanies the no-OCR Android APK. It records the locked Android runtime dependencies and the",
        "Rust dependency lock used to build the native core. The Rust inventory intentionally covers the complete",
        "Cargo.lock (a conservative superset of the Android-selected feature graph) so a dependency cannot lose its",
        "attribution merely because Cargo feature resolution changes.",
        "",
        f"Android lock SHA-256: {sha256(GRADLE_LOCK)}",
        f"Rust lock SHA-256: {sha256(CARGO_LOCK)}",
        "License families represented include Apache License 2.0, MIT License, BSD, Zlib, Unicode, Unlicense, and 0BSD.",
        "",
        "ANDROID RELEASE RUNTIME DEPENDENCIES",
        "------------------------------------",
    ]
    lines.extend(f"- {coordinate} [{license_name}]" for coordinate, license_name in android_rows)
    lines.extend(["", "RUST LOCKED DEPENDENCIES", "------------------------"])
    lines.extend(f"- {name} {version} [{license_info}]" for name, version, license_info in rust_rows)
    lines.extend(["", "UPSTREAM LICENSE AND NOTICE TEXTS", "---------------------------------"])
    for digest in sorted(texts):
        lines.extend(
            [
                "",
                f"=== notice sha256:{digest} ===",
                "Sources:",
                *(f"- {source}" for source in sorted(set(text_sources[digest]))),
                "",
                texts[digest].rstrip(),
            ]
        )
    lines.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())