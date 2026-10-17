#!/usr/bin/env python3
"""Inspección de solo lectura del servicio y sus bases SQLite."""

from __future__ import annotations

import grp
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


SERVICE = "gmv-one-meal-one-speech.service"
REPOSITORY = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = Path("/etc/default/gmv-one-meal-one-speech")
STATE_DIRECTORY = Path("/var/lib/gmv-one-meal-one-speech")
REPOSITORY_INSTANCE = REPOSITORY / "instance"
REPOSITORY_DATABASE = REPOSITORY_INSTANCE / "portal.sqlite3"
SAFE_ENV_KEYS = (
    "APP_ENV",
    "DATABASE_URL",
    "DATABASE_PATH",
    "UPLOAD_FOLDER",
    "COOKIE_SECURE",
    "TRUSTED_PROXY_COUNT",
)


def run_systemctl(*arguments: str) -> str:
    result = subprocess.run(
        ["systemctl", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def read_environment_file(path: Path) -> dict[str, str]:
    """Read simple KEY=value entries without sourcing or printing secrets."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = value.split(" #", maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def process_environment(pid: int) -> dict[str, str]:
    if pid <= 0:
        return {}
    try:
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return {}

    values: dict[str, str] = {}
    for entry in entries:
        key, separator, value = entry.partition(b"=")
        if separator:
            values[key.decode(errors="replace")] = value.decode(errors="replace")
    return values


def effective_value(
    key: str,
    process_values: dict[str, str],
    service_values: dict[str, str],
    dotenv_values: dict[str, str],
    default: str | None = None,
) -> tuple[str | None, str]:
    for source_name, values in (
        ("proceso", process_values),
        ("EnvironmentFile del servicio", service_values),
        (".env del repositorio", dotenv_values),
    ):
        if key in values:
            return values[key], source_name
    return default, "valor predeterminado de la aplicación"


def sqlite_path(database_url: str | None) -> Path | None:
    if database_url is None or not database_url.startswith("sqlite:///"):
        return None
    raw_path = database_url[len("sqlite:///") :].split("?", maxsplit=1)[0]
    if not raw_path or raw_path == ":memory:":
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = REPOSITORY_INSTANCE / path
    return path.resolve(strict=False)


def inspect_database(path: Path) -> None:
    print(f"  Archivo: {path}")
    if not path.is_file():
        print("  Estado: no existe")
        return

    try:
        metadata = path.stat()
        owner = pwd.getpwuid(metadata.st_uid).pw_name
        group = grp.getgrgid(metadata.st_gid).gr_name
        print(
            f"  Estado: existe · {metadata.st_size} bytes · "
            f"propietario {owner}:{group} · modo {metadata.st_mode & 0o777:04o}"
        )
    except (OSError, KeyError) as error:
        print(f"  Metadatos: no disponibles ({error})")

    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            version = "sin tabla alembic_version"
            if "alembic_version" in tables:
                row = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                ).fetchone()
                version = row[0] if row else "sin revisión aplicada"
            users = "no disponible"
            if "user" in tables:
                users = str(connection.execute('SELECT COUNT(*) FROM "user"').fetchone()[0])
            print(f"  Esquema: {version} · cuentas: {users}")
        finally:
            connection.close()
    except sqlite3.Error as error:
        print(f"  Inspección SQLite: no disponible ({error})")


def discover_environment_file(unit_output: str) -> Path | None:
    for line in unit_output.splitlines():
        if not line.startswith("EnvironmentFiles="):
            continue
        value = line.partition("=")[2].strip()
        match = re.match(r"(-?)(\S+?)(?:\s+\(.*\))?$", value)
        if match:
            return Path(match.group(2))
    return DEFAULT_ENV_FILE if DEFAULT_ENV_FILE.exists() else None


def main() -> int:
    if not shutil.which("systemctl"):
        print("ERROR: systemctl no está disponible; ejecuta este script en el servidor Linux.")
        return 1

    print("INSPECCIÓN DE DESPLIEGUE · SOLO LECTURA")
    print(f"Repositorio del script: {REPOSITORY}")
    print(f"Destino repo-local esperado: {REPOSITORY_DATABASE}")
    print("No se modifican servicios, archivos, permisos ni datos.")

    unit_output = run_systemctl(
        "show",
        SERVICE,
        "--property=LoadState,ActiveState,SubState,MainPID,User,Group,WorkingDirectory,"
        "FragmentPath,ExecStart,EnvironmentFiles,Requires",
        "--no-pager",
    )
    if not unit_output:
        print(f"\nServicio {SERVICE}: no se pudo leer (¿existe systemd o la unidad?).")
        return 0

    unit_values: dict[str, str] = {}
    print(f"\nServicio: {SERVICE}")
    for line in unit_output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            unit_values[key] = value
            print(f"  {key}: {value}")

    env_file = discover_environment_file(unit_output)
    service_values = read_environment_file(env_file) if env_file else {}
    dotenv_path = REPOSITORY / ".env"
    dotenv_values = read_environment_file(dotenv_path)
    print(f"\nEnvironmentFile: {env_file or 'no detectado'}")
    print(f".env del repositorio: {'presente' if dotenv_path.is_file() else 'ausente'}")

    try:
        main_pid = int(unit_values.get("MainPID", "0"))
    except ValueError:
        main_pid = 0
    process_values = process_environment(main_pid)
    if main_pid:
        print(f"Proceso principal: PID {main_pid} (se inspeccionan solo variables no secretas).")
    else:
        print("Proceso principal: no activo; se usan EnvironmentFile, .env y valores por defecto.")

    effective: dict[str, str | None] = {}
    print("\nVariables efectivas no secretas:")
    defaults = {
        "APP_ENV": "development",
        "DATABASE_URL": "sqlite:///portal.sqlite3",
        "DATABASE_PATH": None,
        "UPLOAD_FOLDER": None,
        "COOKIE_SECURE": "0",
        "TRUSTED_PROXY_COUNT": "0",
    }
    for key in SAFE_ENV_KEYS:
        value, source = effective_value(
            key,
            process_values,
            service_values,
            dotenv_values,
            default=defaults[key],
        )
        effective[key] = value
        # Never print SECRET_KEY, JWT_SECRET_KEY or the Redis URI.
        if key == "DATABASE_URL":
            display = "SQLite configurado" if value and value.startswith("sqlite:") else (
                "vacío" if value == "" else "no-SQLite o no configurado"
            )
        else:
            display = value if value not in (None, "") else "(vacío/no configurado)"
        print(f"  {key}: {display} [{source}]")

    current_database = sqlite_path(effective.get("DATABASE_URL"))
    print("\nBase que usaría la aplicación:")
    if current_database is None:
        print("  No se pudo resolver como archivo SQLite; revisa el tipo/valor de DATABASE_URL.")
    else:
        inspect_database(current_database)

    print("\nBase objetivo en el checkout del script:")
    inspect_database(REPOSITORY_DATABASE)
    if current_database is not None:
        same_file = current_database == REPOSITORY_DATABASE.resolve(strict=False)
        print(f"  DATABASE_URL apunta a la base repo-local: {'SÍ' if same_file else 'NO'}")

    database_path = effective.get("DATABASE_PATH")
    print("\nDATABASE_PATH usado por update.sh:")
    print(f"  {database_path or '(vacío/no configurado)'}")
    if database_path and current_database:
        backup_path = Path(database_path)
        if not backup_path.is_absolute():
            backup_path = REPOSITORY / backup_path
        print(
            "  Coincide con DATABASE_URL: "
            f"{'SÍ' if backup_path.resolve(strict=False) == current_database else 'NO'}"
        )

    upload_folder = effective.get("UPLOAD_FOLDER")
    if upload_folder:
        upload_path = Path(upload_folder)
        if not upload_path.is_absolute():
            working_directory = Path(unit_values.get("WorkingDirectory") or REPOSITORY)
            upload_path = working_directory / upload_path
    else:
        upload_path = REPOSITORY_INSTANCE / "uploads"
    print(f"\nCarpeta de imágenes efectiva: {upload_path.resolve(strict=False)}")

    print("\nOtros SQLite cercanos:")
    candidates: set[Path] = set()
    for directory in (REPOSITORY_INSTANCE, STATE_DIRECTORY):
        if directory.is_dir():
            candidates.update(
                path.resolve(strict=False)
                for pattern in ("*.sqlite", "*.sqlite3", "*.db")
                for path in directory.glob(pattern)
                if path.is_file()
            )
    if not candidates:
        print("  No se encontraron archivos SQLite en instance/ ni en /var/lib de la aplicación.")
    else:
        for candidate in sorted(candidates):
            print(f"  {candidate}")

    print("\nRedis:")
    redis_state = run_systemctl("is-active", "redis-server.service") or "desconocido/no activo"
    print(f"  redis-server.service: {redis_state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
