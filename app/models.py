from __future__ import annotations

from datetime import UTC, datetime

from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="member")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    auth_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    talks = db.relationship("Talk", back_populates="speaker", cascade="all, delete-orphan")

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Invitation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code_hash = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Talk(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, nullable=False)
    event_at = db.Column(db.DateTime, nullable=False, index=True)  # naive UTC
    status = db.Column(db.String(12), nullable=False, default="scheduled", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    completed_at = db.Column(db.DateTime, nullable=True)
    speaker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)

    speaker = db.relationship("User", back_populates="talks")
    resources = db.relationship(
        "TalkResource", back_populates="talk", cascade="all, delete-orphan", order_by="TalkResource.id"
    )
    images = db.relationship(
        "TalkImage", back_populates="talk", cascade="all, delete-orphan", order_by="TalkImage.position"
    )
    reactions = db.relationship("Reaction", back_populates="talk", cascade="all, delete-orphan")
    comments = db.relationship("Comment", back_populates="talk", cascade="all, delete-orphan")


class TalkResource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(100), nullable=False)
    url = db.Column(db.String(2048), nullable=False)
    talk_id = db.Column(db.Integer, db.ForeignKey("talk.id"), nullable=False, index=True)
    talk = db.relationship("Talk", back_populates="resources")


class TalkImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(80), unique=True, nullable=False)
    alt_text = db.Column(db.String(160), nullable=False, default="Imagen de la exposición")
    position = db.Column(db.Integer, nullable=False, default=0)
    talk_id = db.Column(db.Integer, db.ForeignKey("talk.id"), nullable=False, index=True)
    talk = db.relationship("Talk", back_populates="images")


class Reaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    value = db.Column(db.String(8), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    talk_id = db.Column(db.Integer, db.ForeignKey("talk.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    talk = db.relationship("Talk", back_populates="reactions")
    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("talk_id", "user_id", name="uq_reaction_talk_user"),)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.String(2000), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    talk_id = db.Column(db.Integer, db.ForeignKey("talk.id"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("comment.id"), nullable=True, index=True)
    talk = db.relationship("Talk", back_populates="comments")
    author = db.relationship("User")
    parent = db.relationship("Comment", remote_side=[id], back_populates="replies")
    replies = db.relationship("Comment", back_populates="parent", cascade="all, delete-orphan")
