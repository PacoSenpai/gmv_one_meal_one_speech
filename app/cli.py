from __future__ import annotations

import getpass
import re

import click
from flask import Flask

from app.extensions import db
from app.models import User


def register_cli_commands(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True, help="Nombre de usuario del administrador.")
    @click.option("--name", prompt="Nombre completo (opcional)", default="", show_default=False)
    def create_admin(username: str, name: str) -> None:
        """Create the first administrator without exposing a public admin signup."""
        normalized_username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,30}", normalized_username):
            raise click.ClickException(
                "El usuario debe tener 3-30 caracteres: letras, números, punto, guion o guion bajo."
            )
        if User.query.filter_by(username=normalized_username).first():
            raise click.ClickException("Ese nombre de usuario ya existe.")
        password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
        confirmation = getpass.getpass("Repite la contraseña: ")
        if password != confirmation or len(password) < 12:
            raise click.ClickException("Las contraseñas no coinciden o tienen menos de 12 caracteres.")
        user = User(username=normalized_username, full_name=name.strip() or None, role="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Administrador {normalized_username} creado.")
