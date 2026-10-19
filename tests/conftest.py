from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db
from app.models import User


@pytest.fixture
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-with-more-than-32-bytes",
            "JWT_SECRET_KEY": "test-jwt-secret-key-with-more-than-32-bytes",
            "APP_ENV": "test",
            "DATABASE_PATH": None,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.sqlite3'}",
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "RATELIMIT_STORAGE_URI": "memory://",
            "TRUSTED_PROXY_COUNT": 0,
            "JWT_COOKIE_SECURE": False,
            "SESSION_COOKIE_SECURE": False,
        }
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_user(app):
    with app.app_context():
        user = User(username="admin", full_name="Portal Admin", role="admin", is_superadmin=True)
        user.set_password("a-very-secure-password")
        db.session.add(user)
        db.session.commit()
        return user.id
