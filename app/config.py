from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    APP_ENV: str = "development"
    DEBUG: bool = True

    DATABASE_URL: str = "postgresql+asyncpg://azs:azs_pass@localhost:5432/azs_bonus"
    SYNC_DATABASE_URL: str = "postgresql+psycopg://azs:azs_pass@localhost:5432/azs_bonus"

    REDIS_URL: str = "redis://localhost:6379/0"

    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Admin (simple HTTP Basic)
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change-me"

    SMS_PROVIDER: str = "stub"
    SMS_PROVIDER_API_KEY: str = ""
    SMS_PROVIDER_SENDER: str = ""
    SMS_PROVIDER_INTERNATIONAL_SENDER: str = ""
    SMS_PROVIDER_URL: str = "https://sms.ru/sms/send"
    SMS_PROVIDER_SMS_RU_URL: str = ""
    SMS_PROVIDER_BYTEHAND_URL: str = ""
    SMS_PROVIDER_SMSC_URL: str = ""
    SMS_PROVIDER_FALLBACK: str = ""
    SMS_STUB_EXPOSE_CODE: bool = False
    SMS_CODE_TTL_SECONDS: int = 300
    SMS_RESEND_INTERVAL_SECONDS: int = 60
    SMS_CODE_LENGTH: int = 4
    MOBILE_AUTH_USE_TEST_CODE: bool = False
    MOBILE_AUTH_MAX_CODE_ATTEMPTS: int = 2

    ANDROID_APP_PACKAGE: str = "com.azsbonus.app"
    ANDROID_SHA256_CERT_FINGERPRINTS: str = ""
    PUBLIC_APP_URL: str = "http://localhost:8000"
    APP_TIMEZONE: str = "Asia/Dushanbe"

    MAX_REDEMPTION_PERCENT: int = 100
    BONUS_EXPIRY_MONTHS: int = 12


settings = Settings()
