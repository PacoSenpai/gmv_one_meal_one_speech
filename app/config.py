from __future__ import annotations

import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", SECRET_KEY)
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///portal.sqlite3")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)
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
    MAX_IMAGE_SIZE = 8 * 1024 * 1024
    MAX_IMAGES_PER_TALK = 5
    MAX_IMAGE_PIXELS = 25_000_000
    TIMEZONE = "Europe/Madrid"
    JSON_SORT_KEYS = False
