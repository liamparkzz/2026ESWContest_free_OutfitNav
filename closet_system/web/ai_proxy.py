from __future__ import annotations

import os
import httpx
from fastapi import HTTPException, UploadFile


async def analyze_external(file: UploadFile, ai_cfg: dict) -> dict:
    provider = (ai_cfg.get("provider") or "external_http").lower()
    if provider in {"manual", "none", "disabled"}:
        return {
            "category": "",
            "color": "",
            "season": "",
            "manual_entry_required": True,
            "message": "AI 분석이 비활성화되어 있습니다. 옷 정보를 직접 입력해주세요.",
        }
    if provider == "mock":
        return {
            "category": "상의",
            "subcategory": "반팔티",
            "color": "black",
            "color_name": "검정색",
            "season": "여름",
        }

    endpoint = ai_cfg.get("external_endpoint")
    if not endpoint:
        if bool(ai_cfg.get("fallback_to_manual", True)):
            return {
                "category": "",
                "color": "",
                "season": "",
                "manual_entry_required": True,
                "message": "AI 서버가 설정되지 않아 옷 정보를 직접 입력합니다.",
            }
        raise HTTPException(status_code=500, detail="ai.external_endpoint가 설정되지 않았습니다.")

    raw = await file.read()
    field = ai_cfg.get("external_file_field", "file")
    headers: dict[str, str] = {}
    env_name = ai_cfg.get("external_api_key_env", "")
    if env_name:
        value = os.getenv(env_name, "")
        if value:
            header = ai_cfg.get("external_auth_header", "Authorization")
            prefix = ai_cfg.get("external_auth_prefix", "Bearer").strip()
            headers[header] = f"{prefix} {value}".strip()

    extra_fields = ai_cfg.get("external_extra_fields", {}) or {}
    timeout = float(ai_cfg.get("external_timeout_seconds", 60.0))
    files = {field: (file.filename or "photo.jpg", raw, file.content_type or "application/octet-stream")}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(endpoint, files=files, data=extra_fields, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 서버 연결 실패: {e}") from e

    if not res.is_success:
        raise HTTPException(status_code=502, detail=f"AI 서버 HTTP {res.status_code}: {res.text[:500]}")
    try:
        return res.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail="AI 서버 응답이 JSON이 아닙니다.") from e
