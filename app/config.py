from __future__ import annotations

import os
from datetime import timedelta


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
    SECRET_KEY = os.environ.get("SECRET_KEY")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///portal.sqlite3")
    DATABASE_PATH = os.environ.get("DATABASE_PATH")
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
    JWT_SESSION_COOKIE = False
    SESSION_REMEMBER_DAYS = _env_int("SESSION_REMEMBER_DAYS", 30)
    JWT_COOKIE_CSRF_PROTECT = True
    JWT_CSRF_CHECK_FORM = True
    JWT_CSRF_IN_COOKIES = True
    JWT_ACCESS_CSRF_COOKIE_NAME = "csrf_access_token"
    JWT_ACCESS_CSRF_HEADER_NAME = "X-CSRF-TOKEN"
    JWT_COOKIE_SAMESITE = "Lax"
    JWT_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
    JWT_ACCESS_COOKIE_PATH = "/"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
    MAX_CONTENT_LENGTH = 42 * 1024 * 1024
    MAX_FORM_MEMORY_SIZE = 128 * 1024
    MAX_FORM_PARTS = 64
    MAX_IMAGE_SIZE = 8 * 1024 * 1024
    MAX_IMAGES_PER_TALK = 5
    MAX_IMAGE_PIXELS = 25_000_000
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI") or (
        "redis://127.0.0.1:6379/0" if DATABASE_PATH or APP_ENV == "production" else "memory://"
    )
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_SWALLOW_ERRORS = False
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
    TIMEZONE = "Europe/Madrid"
    JSON_SORT_KEYS = False
