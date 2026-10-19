import re

from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates


class RegistrationSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    username = fields.String(required=True, validate=validate.Length(min=3, max=30))
    full_name = fields.String(load_default="", validate=validate.Length(max=120))
    password = fields.String(required=True, validate=validate.Length(min=12, max=256))

    @validates("username")
    def validate_username(self, value: str, **_kwargs: object) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,30}", value.strip()):
            raise ValidationError("Usa letras, números, puntos, guiones o guiones bajos.")


class ProposalSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    title = fields.String(required=True, validate=validate.Length(min=3, max=140))
    description = fields.String(required=True, validate=validate.Length(min=20, max=8000))

    @validates("title")
    def validate_title(self, value: str, **_kwargs: object) -> None:
        if len(value.strip()) < 3:
            raise ValidationError("El título debe tener al menos tres caracteres no vacíos.")

    @validates("description")
    def validate_description(self, value: str, **_kwargs: object) -> None:
        if len(value.strip()) < 20:
            raise ValidationError("La descripción debe tener al menos 20 caracteres no vacíos.")


class CommentSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    body = fields.String(required=True, validate=validate.Length(min=2, max=2000))

    @validates("body")
    def validate_body(self, value: str, **_kwargs: object) -> None:
        if len(value.strip()) < 2:
            raise ValidationError("El comentario no puede estar vacío.")


class AccountSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    username = fields.String(required=True, validate=validate.Length(min=3, max=30))
    full_name = fields.String(load_default="", validate=validate.Length(max=120))

    @validates("username")
    def validate_username(self, value: str, **_kwargs: object) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,30}", value.strip()):
            raise ValidationError("Usa letras, números, puntos, guiones o guiones bajos.")


class PasswordSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    current_password = fields.String(load_default="")
    new_password = fields.String(required=True, validate=validate.Length(min=12, max=256))
