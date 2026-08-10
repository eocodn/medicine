from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


STATUS_SOURCES = {
    "hira_reimbursement": {
        "name": "건강보험심사평가원 약가기준정보",
        "url": "https://apis.data.go.kr/B551182/dgamtCrtrInfoService1.2/getDgamtList",
        "params": {},
    },
    "mfds_supply_stop": {
        "name": "식약처 의약품 생산·수입·공급중단",
        "url": "https://apis.data.go.kr/1471000/MdcinPrdctnIncmeSuplyService2/getMdcinPrdctnIncmeSuplyList",
        "params": {"type": "json"},
    },
    "mfds_supply_shortage": {
        "name": "식약처 의약품 공급부족",
        "url": "https://apis.data.go.kr/1471000/MdcinSuplyLackService03/getMdcinSuplyLackList01",
        "params": {"type": "json"},
    },
}


PERMISSION_ERROR_MARKERS = {
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "SERVICE_ACCESS_DENIED_ERROR",
    "PERMISSION_DENIED",
    "등록되지 않은 서비스키",
    "서비스 접근거부",
}


def _extract_message(body: str) -> str | None:
    body = body.strip()
    if not body:
        return None
    try:
        payload = json.loads(body)
        header = payload.get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader", {})
        if header:
            return header.get("errMsg") or header.get("returnAuthMsg")
        response = payload.get("response", payload)
        header = response.get("header", {}) if isinstance(response, dict) else {}
        return header.get("resultMsg") or header.get("resultCode")
    except (json.JSONDecodeError, AttributeError):
        pass
    try:
        root = ET.fromstring(body)
        for tag in ("errMsg", "returnAuthMsg", "resultMsg", "resultCode"):
            node = root.find(f".//{tag}")
            if node is not None and node.text:
                return node.text.strip()
    except ET.ParseError:
        pass
    return None


def classify_probe_response(http_status: int, body: str) -> dict:
    message = _extract_message(body)
    combined = f"{message or ''} {body[:500]}"
    if any(marker in combined for marker in PERMISSION_ERROR_MARKERS):
        return {"status": "permission_required", "http_status": http_status, "message": message}
    if 200 <= http_status < 300:
        return {"status": "available", "http_status": http_status, "message": message}
    return {"status": "error", "http_status": http_status, "message": message}


def probe_status_sources(service_key: str, timeout: int = 10) -> dict:
    service_key = service_key.strip()
    if not service_key:
        raise ValueError("service key is required")

    results = {}
    for key, source in STATUS_SOURCES.items():
        params = {
            "serviceKey": service_key,
            "pageNo": 1,
            "numOfRows": 1,
            **source["params"],
        }
        query = urllib.parse.urlencode(params, safe="%")
        request = urllib.request.Request(f"{source['url']}?{query}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                result = classify_probe_response(response.status, body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            result = classify_probe_response(exc.code, body)
        except OSError as exc:
            result = {"status": "error", "http_status": None, "message": str(exc)}
        results[key] = {"name": source["name"], **result}
    return results
