from __future__ import annotations

import csv
import hashlib
import http.cookiejar
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


BRIDGE_FILENAME = "hira_product_code_bridge.csv"
DATASET_KEY = "product_bridge:hira_standard_code"
PORTAL_DATASET_ID = "15067462"
PORTAL_DETAIL_ID = "uddi:456729a5-28ed-494d-b5a8-ba5000eb6bab"
PORTAL_DETAIL_URL = f"https://www.data.go.kr/data/{PORTAL_DATASET_ID}/fileData.do"
PORTAL_META_URL = "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do"
PORTAL_DOWNLOAD_URL = "https://www.data.go.kr/cmm/cmm/fileDownload.do"
REQUIRED_HEADERS = {"한글상품명", "품목기준코드", "표준코드", "제품코드(개정후)"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_product_code_bridge_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS product_code_bridge (
            dataset_key TEXT NOT NULL,
            item_seq TEXT NOT NULL,
            product_code TEXT NOT NULL,
            product_name TEXT,
            PRIMARY KEY(item_seq, product_code)
        );
        CREATE INDEX IF NOT EXISTS idx_product_code_bridge_code
            ON product_code_bridge(product_code);
        """
    )


def _decode_header(path: Path) -> tuple[str, list[str]]:
    for encoding in ("cp949", "utf-8-sig"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                header = next(csv.reader(handle))
        except (UnicodeDecodeError, StopIteration):
            continue
        normalized = [str(value).strip() for value in header]
        if REQUIRED_HEADERS.issubset(normalized):
            return encoding, normalized
    raise ValueError("HIRA product-code bridge CSV has an unsupported encoding or header")


def import_product_code_bridge(con: sqlite3.Connection, raw_dir: str | Path) -> tuple[int, int]:
    create_product_code_bridge_schema(con)
    path = Path(raw_dir) / BRIDGE_FILENAME
    if not path.exists():
        return 0, 0

    encoding, headers = _decode_header(path)
    source_rows = 0
    before = con.total_changes
    with path.open("r", encoding=encoding, newline="") as handle:
        for row in csv.DictReader(handle):
            source_rows += 1
            item_seq = str(row.get("품목기준코드") or "").strip()
            product_code = str(row.get("제품코드(개정후)") or "").strip()
            if not item_seq or not product_code:
                continue
            con.execute(
                """
                INSERT OR IGNORE INTO product_code_bridge(
                    dataset_key,item_seq,product_code,product_name
                ) VALUES(?,?,?,?)
                """,
                (DATASET_KEY, item_seq, product_code, str(row.get("한글상품명") or "").strip() or None),
            )
    imported = con.total_changes - before
    con.execute(
        """
        INSERT INTO source_files(
            dataset_key,source_kind,category,source_path,sha256,size_bytes,row_count,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            DATASET_KEY,
            "product_bridge",
            "hira_standard_code",
            str(path),
            _sha256(path),
            path.stat().st_size,
            imported,
            json.dumps(
                {
                    "header": headers,
                    "encoding": encoding,
                    "source_rows": source_rows,
                    "portal_dataset_id": PORTAL_DATASET_ID,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
    )
    return 1, imported


def _open_portal_download(timeout: int):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": "Mozilla/5.0", "Referer": PORTAL_DETAIL_URL}
    opener.open(urllib.request.Request(PORTAL_DETAIL_URL, headers=headers), timeout=timeout).read()
    body = urllib.parse.urlencode(
        {
            "publicDataDetailPk": PORTAL_DETAIL_ID,
            "publicDataPk": PORTAL_DATASET_ID,
            "atchFileId": "",
            "fileDetailSn": "1",
            "publicDataTyCode": "PR0051",
        }
    ).encode()
    meta_request = urllib.request.Request(
        PORTAL_META_URL,
        data=body,
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )
    metadata = json.loads(opener.open(meta_request, timeout=timeout).read().decode("utf-8", "replace"))
    if metadata.get("status") is not True or not metadata.get("atchFileId"):
        raise RuntimeError(metadata.get("error") or "data.go.kr did not authorize the public HIRA file download")
    params = urllib.parse.urlencode(
        {
            "atchFileId": metadata["atchFileId"],
            "fileDetailSn": metadata.get("fileDetailSn") or "1",
            "insertDataPrcus": "N",
        }
    )
    return opener.open(
        urllib.request.Request(f"{PORTAL_DOWNLOAD_URL}?{params}", headers=headers),
        timeout=timeout,
    ), metadata


def sync_product_code_bridge(
    raw_dir: str | Path,
    *,
    progress: bool = True,
    timeout: int = 60,
) -> dict:
    root = Path(raw_dir)
    root.mkdir(parents=True, exist_ok=True)
    output = root / BRIDGE_FILENAME
    partial = output.with_suffix(output.suffix + ".part")
    partial.unlink(missing_ok=True)
    started = time.monotonic()
    response, metadata = _open_portal_download(timeout)
    downloaded = 0
    next_report = 5 * 1024 * 1024
    try:
        with partial.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress and downloaded >= next_report:
                    print(f"[sync] HIRA product-code bridge: {downloaded / (1024 * 1024):.1f} MiB", file=sys.stderr, flush=True)
                    next_report += 5 * 1024 * 1024
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        response.close()
    encoding, headers = _decode_header(partial)
    os.replace(partial, output)
    return {
        "path": str(output),
        "size_bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "encoding": encoding,
        "header": headers,
        "attachment_id": metadata.get("atchFileId"),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


__all__ = [
    "BRIDGE_FILENAME",
    "DATASET_KEY",
    "REQUIRED_HEADERS",
    "create_product_code_bridge_schema",
    "import_product_code_bridge",
    "sync_product_code_bridge",
]
