# АЗС Бонус — Бонусная система для сети АЗС и магазина

## Стек

| Компонент | Версия |
|-----------|--------|
| Python | 3.12 |
| FastAPI | 0.111 |
| SQLAlchemy | 2.0 (async) |
| PostgreSQL | 15 |
| Redis | 7 |
| Celery | 5.3 |
| Alembic | 1.13 |

## Структура проекта

```
azs-bonus/
├── app/
│   ├── api/
│   │   ├── cash.py          # POST /api/cash/{accrue,redeem,client-info}
│   │   ├── mobile.py        # /api/mobile/auth, /profile, /transactions, /qrcode, /rules
│   │   ├── admin.py         # CRUD правил, акций, кассиров
│   │   └── reports.py       # Отчёты (JSON + CSV)
│   ├── db/
│   │   ├── base.py
│   │   ├── models.py        # SQLAlchemy-модели
│   │   └── session.py
│   ├── services/
│   │   ├── accrual.py       # Расчёт бонусов + акции
│   │   ├── bonus_expiry.py  # FIFO-списание, ежедневное сгорание
│   │   ├── transactions.py  # accrue / redeem (атомарность, идемпотентность)
│   │   ├── auth.py          # JWT
│   │   ├── sms.py           # OTP (stub / реальный провайдер)
│   │   └── qr.py            # QR-генератор
│   ├── views/
│   │   └── frontend.py      # Jinja2-страницы
│   ├── templates/
│   │   ├── base.html
│   │   ├── cashier.html
│   │   ├── reports.html
│   │   └── admin/
│   │       ├── dashboard.html
│   │       └── rules.html
│   ├── celery_app.py        # Настройки Celery + beat-расписание
│   ├── tasks.py             # expire_bonuses_task
│   ├── config.py            # Pydantic-Settings
│   └── main.py              # FastAPI app
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
├── tests/
│   ├── conftest.py
│   ├── test_transactions.py
│   ├── test_api_cash.py
│   └── test_bonus_expiry.py
├── Dockerfile
├── scripts/
│   └── run_checks.ps1      # Единый запуск smoke/core/full тестов
├── docker-compose.yml
├── alembic.ini
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

## Быстрый старт (Docker)

```bash
# 1. Скопировать конфиг
cp .env.example .env

# 2. Собрать и запустить
docker compose up --build -d

# 3. Миграции применяются автоматически сервисом migrate
# 4. Приложение доступно на http://localhost:8000
```

## Локальная разработка

```bash
# Создать виртуальное окружение
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt -r requirements-dev.txt
pip install python-dateutil psycopg2-binary aiosqlite

# Поднять postgres + redis
docker compose up postgres redis -d

# Применить миграции
export SYNC_DATABASE_URL=postgresql+psycopg://azs:azs_pass@localhost:5432/azs_bonus
alembic upgrade head

# Запустить приложение
uvicorn app.main:app --reload

# Celery-worker (в отдельном терминале)
celery -A app.celery_app worker --loglevel=info

# Celery-beat
celery -A app.celery_app beat --loglevel=info
```

## Тесты

```bash
pytest --cov=app --cov-report=term-missing
```

Для компактного запуска основных проверок на Windows:

```powershell
.\scripts\run_checks.ps1
.\scripts\run_checks.ps1 -Suite smoke
.\scripts\run_checks.ps1 -Suite full
```

Наборы:
- `smoke` — быстрые проверки ключевой бизнес-логики
- `core` — основной рабочий набор API и UI-backend тестов
- `full` — весь каталог `tests`

## API

### Кассирский интерфейс (JSON)

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/api/cash/accrue` | Начислить бонусы по чеку |
| POST | `/api/cash/redeem` | Списать бонусы |
| POST | `/api/cash/client-info` | Информация о клиенте |

**Пример начисления:**
```json
POST /api/cash/accrue
{
  "phone": "+79001234567",
  "purchase_amount": 1500.00,
  "fuel_liters": 30.5,
  "cashier_id": 1,
  "check_id": "0001-20250101-001",
  "location": "fuel"
}
```

### Мобильный API

| Метод | URL | Описание |
|-------|-----|----------|
| POST | `/api/mobile/auth/send-code` | Отправить OTP |
| POST | `/api/mobile/auth/verify` | Проверить OTP → JWT |
| GET | `/api/mobile/profile` | Профиль клиента |
| GET | `/api/mobile/transactions` | История транзакций |
| GET | `/api/mobile/qrcode` | QR-код (base64 PNG) |
| GET | `/api/mobile/rules` | Правила начисления |

### Администрирование

| Метод | URL | Описание |
|-------|-----|----------|
| GET/POST | `/api/admin/rules` | Правила начисления |
| PUT/DELETE | `/api/admin/rules/{id}` | Изменить/удалить правило |
| GET/POST | `/api/admin/campaigns` | Акции |
| GET/POST/DELETE | `/api/admin/cashiers` | Кассиры |

### Отчёты

```
GET /api/reports/cashier-sales?date_from=2025-01-01&date_to=2025-01-31
GET /api/reports/fuel-by-type?date_from=2025-01-01&date_to=2025-01-31&format=csv
GET /api/reports/bonus-movement?date_from=2025-01-01&date_to=2025-01-31
```

## Бизнес-логика

### Уровни клиентов
| Уровень | Сумма трат |
|---------|-----------|
| Бронза | 0 – 5 000 ₽ |
| Серебро | 5 001 – 15 000 ₽ |
| Золото | > 15 000 ₽ |

### Начисление
- Рассчитывается по правилам таблицы `accrual_rules` (процент от чека, бонусы за литр или фиксированная сумма).
- Активные акции (`campaigns`) умножают начисление на `multiplier` (например, ×2 по пятницам 18:00–22:00).
- Начисленные бонусы сгорают через 12 месяцев (ежедневная задача Celery).

### Списание
- Не более 30% суммы чека.
- 1 бонус = 1 рубль.
- Списание идёт по FIFO из `bonus_expiry` (сначала сгорающие раньше).

### Атомарность
- Все операции начисления/списания выполняются в транзакции с `SELECT FOR UPDATE`.
- Идемпотентность гарантируется уникальностью `check_id`.

## Переменные окружения

| Переменная | Описание | Пример |
|-----------|---------|--------|
| `DATABASE_URL` | Async DSN PostgreSQL | `postgresql+asyncpg://...` |
| `SYNC_DATABASE_URL` | Sync DSN (Alembic) | `postgresql+psycopg2://...` |
| `REDIS_URL` | Redis DSN | `redis://redis:6379/0` |
| `SECRET_KEY` | JWT-секрет | `long-random-string` |
| `MAX_REDEMPTION_PERCENT` | Макс. % чека для списания | `100` |
| `BONUS_EXPIRY_MONTHS` | Срок жизни бонусов (мес.) | `12` |
| `SMS_PROVIDER` | Провайдер SMS (`stub`, `sms_ru`, `bytehand`, `smsc`) | `smsc` |
| `SMS_PROVIDER_API_KEY` | Ключ реального SMS-провайдера | `...` |
| `SMS_PROVIDER_SENDER` | Имя/номер отправителя SMS | `AZSBonus` |
| `SMS_PROVIDER_INTERNATIONAL_SENDER` | Отдельный sender для международных номеров | `AZSBonus` |
| `SMS_PROVIDER_URL` | Endpoint провайдера, если нужен override | `https://smsc.ru/sys/send.php` |
| `SMS_PROVIDER_SMS_RU_URL` | Отдельный endpoint override для `sms_ru` | `https://sms.ru/sms/send` |
| `SMS_PROVIDER_BYTEHAND_URL` | Отдельный endpoint override для `bytehand` | `https://api.bytehand.com/v2/sms/messages` |
| `SMS_PROVIDER_SMSC_URL` | Отдельный endpoint override для `smsc` | `https://smsc.ru/sys/send.php` |
| `SMS_PROVIDER_FALLBACK` | Резервный провайдер при ошибке основного | `smsc` |
| `ANDROID_APP_PACKAGE` | Android package name для TWA | `com.azsbonus.app` |
| `ANDROID_SHA256_CERT_FINGERPRINTS` | SHA-256 отпечатки signing certificate через запятую | `AA:BB:...` |
| `PUBLIC_APP_URL` | Публичный HTTPS URL приложения | `https://example.com` |

## Google Play / TWA

- клиентская privacy policy: `/privacy`
- Digital Asset Links endpoint: `/.well-known/assetlinks.json`
- мобильный manifest подготовлен для PWA/TWA публикации

Перед публикацией заполните реальные значения `ANDROID_APP_PACKAGE`, `ANDROID_SHA256_CERT_FINGERPRINTS`, `PUBLIC_APP_URL` и подключите реальный `SMS_PROVIDER`.

Для Таджикистана можно переключить отправку на `smsc` или оставить основной провайдер и указать `SMS_PROVIDER_FALLBACK=smsc`, чтобы международные ошибки не упирались в один маршрут.

Для Windows-сборки TWA используйте [scripts/google_play_twa.ps1](scripts/google_play_twa.ps1):

- проверка готовности: `./scripts/google_play_twa.ps1 -Action check -BaseUrl https://example.com`
- инициализация Android-обёртки: `./scripts/google_play_twa.ps1 -Action init`
- сборка после инициализации: `./scripts/google_play_twa.ps1 -Action build`
