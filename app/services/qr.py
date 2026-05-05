from __future__ import annotations

import io
import base64
import qrcode

from app.services.transactions import normalize_phone


QR_CLIENT_PREFIX = 'AZSCLIENT:'


def make_client_qr_payload(phone: str) -> str:
    """Builds a stable QR payload for client identification at the cashier."""
    return f"{QR_CLIENT_PREFIX}{normalize_phone(phone)}"


def extract_phone_from_qr_data(qr_data: str) -> str:
    """Extracts client phone from supported QR payload formats."""
    raw_value = str(qr_data or '').strip()
    if not raw_value:
        raise ValueError('QR data is empty')

    normalized_prefix = QR_CLIENT_PREFIX.lower()
    lower_value = raw_value.lower()
    candidates = [raw_value]

    if lower_value.startswith(normalized_prefix):
        candidates.insert(0, raw_value[len(QR_CLIENT_PREFIX):])
    if lower_value.startswith('tel:'):
        candidates.insert(0, raw_value[4:])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return normalize_phone(candidate)
        except ValueError:
            continue

    raise ValueError('Unsupported QR payload')


def generate_qr_base64(data: str) -> str:
    """Генерирует QR-код и возвращает base64-строку PNG."""
    qr = qrcode.QRCode(version=1, box_size=8, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()
