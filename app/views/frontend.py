from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccrualRule, Campaign, Cashier, Client, FuelSale, Transaction
from app.db.session import get_db
from app.config import settings
from app.services.cashier_auth import CASHIER_SESSION_COOKIE, decode_cashier_token
from app.services.admin_auth import is_admin_authenticated
from app.services.suspicious_activity import build_suspicious_activity_alerts
from app.utils.datetime import local_today

router = APIRouter(tags=["frontend"])
templates = Jinja2Templates(directory="app/templates")
STATIC_DIR = Path("app/static")


async def _get_authenticated_cashier(request: Request, db: AsyncSession) -> Cashier | None:
    token = request.cookies.get(CASHIER_SESSION_COOKIE)
    if not token:
        return None
    cashier_id = decode_cashier_token(token)
    if cashier_id is None:
        return None
    cashier = (await db.execute(select(Cashier).where(Cashier.id == cashier_id))).scalar_one_or_none()
    if cashier is None or not cashier.is_active:
        return None
    return cashier


def _login_redirect(request: Request, *, role: str) -> RedirectResponse:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    query = urlencode({"next": next_path, "role": role})
    return RedirectResponse(url=f"/staff-login?{query}", status_code=303)


def _safe_next_path(raw_value: str | None, fallback: str) -> str:
    candidate = (raw_value or fallback).strip() or fallback
    if not candidate.startswith("/"):
        return fallback
    return candidate


# ---- Мобильный клиент ----------------------------------------------------- #

@router.get("/mobile", response_class=HTMLResponse)
async def mobile_page(request: Request):
    return templates.TemplateResponse(request, "mobile.html")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy_page(request: Request):
    return templates.TemplateResponse(
        request,
        "privacy.html",
        {
            "show_admin_menu": False,
            "public_app_url": settings.PUBLIC_APP_URL.rstrip("/"),
            "sms_provider": settings.SMS_PROVIDER,
        },
    )


@router.get("/.well-known/assetlinks.json")
async def assetlinks_json():
    fingerprints = [
        item.strip()
        for item in settings.ANDROID_SHA256_CERT_FINGERPRINTS.split(",")
        if item.strip()
    ]
    if not fingerprints:
        return JSONResponse([])

    return JSONResponse(
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": settings.ANDROID_APP_PACKAGE,
                    "sha256_cert_fingerprints": fingerprints,
                },
            }
        ]
    )


@router.get("/staff-login", response_class=HTMLResponse)
async def staff_login_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    role = (request.query_params.get("role") or "cashier").strip().lower()
    next_path = _safe_next_path(request.query_params.get("next"), "/admin" if role == "admin" else "/cashier")

    is_admin = is_admin_authenticated(request)
    if is_admin:
        return RedirectResponse(url=next_path, status_code=303)

    cashier = await _get_authenticated_cashier(request, db)
    if role in {"cashier", "staff", "reports"} and cashier is not None and role != "admin":
        return RedirectResponse(url="/cashier", status_code=303)

    return templates.TemplateResponse(
        request,
        "staff_login.html",
        {
            "role": role,
            "show_admin_menu": False,
        },
    )


@router.get("/manifest.webmanifest")
async def web_manifest():
    return JSONResponse(
        {
            "id": "/mobile",
            "name": "АЗС Бонус",
            "short_name": "АЗС Бонус",
            "description": "Бонусная программа для клиентов АЗС и магазинов.",
            "start_url": "/mobile",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f0f2f5",
            "theme_color": "#0d6efd",
            "orientation": "portrait",
            "lang": "ru",
            "categories": ["business", "shopping", "productivity"],
            "prefer_related_applications": False,
            "icons": [
                {
                    "src": "/static/icons/azs-bonus-icon.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any"
                },
                {
                    "src": "/static/icons/azs-bonus-maskable.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "maskable"
                },
            ],
        }
    )


@router.get("/sw.js")
async def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


# ---- Кассир ---------------------------------------------------------------- #

@router.get("/cashier", response_class=HTMLResponse)
async def cashier_page(request: Request, db: AsyncSession = Depends(get_db)):
    is_admin = is_admin_authenticated(request)
    if is_admin:
        return templates.TemplateResponse(request, "cashier.html", {"show_admin_menu": True})
    cashier = await _get_authenticated_cashier(request, db)
    if cashier is None:
        return _login_redirect(request, role="staff")
    return templates.TemplateResponse(request, "cashier.html")


@router.get("/fuel-prices", response_class=HTMLResponse)
async def fuel_prices_page(request: Request, db: AsyncSession = Depends(get_db)):
    is_admin = is_admin_authenticated(request)
    if is_admin:
        return templates.TemplateResponse(request, "fuel_prices.html", {"show_admin_menu": True})
    cashier = await _get_authenticated_cashier(request, db)
    if cashier is None:
        return _login_redirect(request, role="staff")
    return templates.TemplateResponse(request, "fuel_prices.html")


@router.get("/cashier/clients", response_class=HTMLResponse)
async def cashier_clients_page(request: Request, db: AsyncSession = Depends(get_db)):
    is_admin = is_admin_authenticated(request)
    if is_admin:
        return templates.TemplateResponse(request, "cashier/clients.html", {"show_admin_menu": True})
    cashier = await _get_authenticated_cashier(request, db)
    if cashier is None:
        return _login_redirect(request, role="staff")
    return templates.TemplateResponse(request, "cashier/clients.html")


@router.post("/cashier/lookup", response_class=HTMLResponse)
async def cashier_lookup(
    request: Request,
    phone: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    is_admin = is_admin_authenticated(request)
    if is_admin:
        stmt = select(Client).where(Client.phone == phone)
        result = await db.execute(stmt)
        client = result.scalar_one_or_none()
        return templates.TemplateResponse(
            request,
            "cashier.html",
            {"client": client, "phone": phone, "show_admin_menu": True},
        )
    cashier = await _get_authenticated_cashier(request, db)
    if cashier is None:
        return _login_redirect(request, role="staff")
    stmt = select(Client).where(Client.phone == phone)
    result = await db.execute(stmt)
    client = result.scalar_one_or_none()
    return templates.TemplateResponse(
        request,
        "cashier.html",
        {"client": client, "phone": phone},
    )


# ---- Admin Dashboard ------------------------------------------------------- #

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not is_admin_authenticated(request):
        return _login_redirect(request, role="admin")
    clients_count = (await db.execute(select(func.count(Client.id)))).scalar()
    tx_count = (await db.execute(select(func.count(Transaction.id)))).scalar()
    total_bonus = (await db.execute(select(func.sum(Transaction.amount_bonus)))).scalar() or 0

    # Продажи за 7 дней
    sales_stmt = (
        select(func.date(FuelSale.sale_date).label("day"), func.sum(FuelSale.total_rub).label("total"))
        .group_by(func.date(FuelSale.sale_date))
        .order_by(func.date(FuelSale.sale_date).desc())
        .limit(7)
    )
    sales_result = await db.execute(sales_stmt)
    sales_data = [{"day": str(r.day), "total": float(r.total or 0)} for r in sales_result.all()]
    suspicious_alerts = await build_suspicious_activity_alerts(db)

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "show_admin_menu": True,
            "clients_count": clients_count,
            "tx_count": tx_count,
            "total_bonus": total_bonus,
            "sales_data": sales_data,
            "suspicious_alerts": suspicious_alerts,
        },
    )


# ---- Admin Rules ----------------------------------------------------------- #

@router.get("/admin/rules", response_class=HTMLResponse)
async def admin_rules_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not is_admin_authenticated(request):
        return _login_redirect(request, role="admin")
    result = await db.execute(select(AccrualRule).order_by(AccrualRule.id))
    rules = result.scalars().all()
    return templates.TemplateResponse(request, "admin/rules.html", {"rules": rules, "show_admin_menu": True})


@router.get("/admin/cashiers", response_class=HTMLResponse)
async def admin_cashiers_page(request: Request):
    if not is_admin_authenticated(request):
        return _login_redirect(request, role="admin")
    return templates.TemplateResponse(request, "admin/cashiers.html", {"show_admin_menu": True})


# ---- Reports --------------------------------------------------------------- #

@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, db: AsyncSession = Depends(get_db)):
    cashier = await _get_authenticated_cashier(request, db)
    is_admin = is_admin_authenticated(request)
    if not is_admin and cashier is None:
        return _login_redirect(request, role="staff")
    response = templates.TemplateResponse(
        request,
        "reports.html",
        {"today": local_today().isoformat(), "show_admin_menu": is_admin},
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
