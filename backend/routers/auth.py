from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import (ADMIN_USER, clear_login_failures, client_ip, create_admin_token,
                       credentials_valid, login_locked, register_login_failure,
                       require_admin)

router = APIRouter(tags=["auth"])


@router.post("/api/auth/login")
async def admin_login(body: Dict, request: Request):
    ip = client_ip(request)
    wait = login_locked(ip)
    if wait is not None:
        raise HTTPException(status_code=429,
                            detail=f"Zu viele Fehlversuche – bitte in {max(1, wait // 60 + 1)} min erneut")
    user = (body.get("username") or "").strip()
    pw = body.get("password") or ""
    if credentials_valid(user, pw):
        clear_login_failures(ip)
        return {"token": create_admin_token(), "user": ADMIN_USER}
    register_login_failure(ip)
    raise HTTPException(status_code=401, detail="Falsche Zugangsdaten")


@router.get("/api/auth/verify")
async def admin_verify(_: bool = Depends(require_admin)):
    return {"valid": True}
