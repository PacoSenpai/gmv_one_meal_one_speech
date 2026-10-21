#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="gmv-one-meal-one-speech.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
APP_DIR="$(git -C "$(dirname "${SCRIPT_PATH}")" rev-parse --show-toplevel)"

if [[ "${EUID}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Error: sudo es necesario para instalar el servicio systemd." >&2
    exit 1
  fi
  if sudo test -f "${SERVICE_PATH}"; then
    echo "El servicio ${SERVICE_NAME} ya existe. No se ha modificado nada."
    exit 0
  fi
  exec sudo --preserve-env=APP_USER bash "$0" "$@"
fi

if [[ -f "${SERVICE_PATH}" ]]; then
  echo "El servicio ${SERVICE_NAME} ya existe. No se ha modificado nada."
  exit 0
fi

APP_USER="${APP_USER:-${SUDO_USER:-}}"
if [[ -z "${APP_USER}" ]] || ! id "${APP_USER}" >/dev/null 2>&1; then
  echo "Error: ejecuta el script con sudo desde la cuenta que ejecutará la aplicación." >&2
  exit 1
fi
if [[ "${APP_USER}" == "root" ]]; then
  echo "Error: la aplicación no debe ejecutarse como root; usa una cuenta propietaria del checkout." >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "Error: este despliegue requiere systemd y Python 3." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl es necesario para comprobar el endpoint de salud." >&2
  exit 1
fi

APP_GROUP="$(id -gn "${APP_USER}")"
INSTANCE_DIR="${APP_DIR}/instance"
DATABASE_PATH="${INSTANCE_DIR}/portal.sqlite3"
UPLOAD_FOLDER="${INSTANCE_DIR}/uploads"
ENV_FILE="/etc/default/gmv-one-meal-one-speech"
umask 027
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${INSTANCE_DIR}" "${UPLOAD_FOLDER}"
if ! sudo -u "${APP_USER}" test -x "${APP_DIR}" \
  || ! sudo -u "${APP_USER}" test -w "${INSTANCE_DIR}"; then
  echo "Error: ${APP_USER} necesita acceso al checkout y escritura en ${INSTANCE_DIR}." >&2
  exit 1
fi

if [[ -f "${ENV_FILE}" ]]; then
  python3 - "${ENV_FILE}" "${DATABASE_PATH}" "${UPLOAD_FOLDER}" <<'PY'
from pathlib import Path
import sys

environment_file = Path(sys.argv[1])
expected = {
    "DATABASE_URL": "sqlite:///portal.sqlite3",
    "DATABASE_PATH": sys.argv[2],
    "UPLOAD_FOLDER": sys.argv[3],
}
configured = {}
for line in environment_file.read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("=")
    if separator:
        configured[key.strip()] = value.strip()
for key, value in expected.items():
    if configured.get(key) != value:
        print(
            f"Error: {environment_file} ya existe y {key} no apunta al checkout actual. "
            "No se ha cambiado la configuración ni la base de datos.",
            file=sys.stderr,
        )
        sys.exit(1)
PY
fi

if [[ ! -d "${APP_DIR}/.venv" ]]; then
  sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

if [[ ! -f "${ENV_FILE}" ]]; then
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  JWT_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat >"${ENV_FILE}" <<EOF
SECRET_KEY=${SECRET_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
DATABASE_URL=sqlite:///portal.sqlite3
DATABASE_PATH=${DATABASE_PATH}
UPLOAD_FOLDER=${UPLOAD_FOLDER}
COOKIE_SECURE=1
APP_ENV=production
RATELIMIT_STORAGE_URI=redis://127.0.0.1:6379/0
TRUSTED_PROXY_COUNT=0
SESSION_REMEMBER_DAYS=30
EOF
  chown root:"${APP_GROUP}" "${ENV_FILE}"
  chmod 0640 "${ENV_FILE}"
fi

for state_file in "${DATABASE_PATH}" "${DATABASE_PATH}-wal" "${DATABASE_PATH}-shm"; do
  if [[ -e "${state_file}" ]]; then
    chown "${APP_USER}:${APP_GROUP}" "${state_file}"
  fi
done
chown -R "${APP_USER}:${APP_GROUP}" "${UPLOAD_FOLDER}"

sudo -u "${APP_USER}" bash -c '
  set -a
  # The deployment-owned environment file is readable by the application group.
  # shellcheck disable=SC1090
  . "$1"
  set +a
  cd "$2"
  exec "$2/.venv/bin/flask" --app wsgi:app db upgrade
' _ "${ENV_FILE}" "${APP_DIR}"

APP_DIR="${APP_DIR}" APP_USER="${APP_USER}" APP_GROUP="${APP_GROUP}" python3 - \
  "${APP_DIR}/deploy/gmv-one-meal-one-speech.service.in" "${SERVICE_PATH}" <<'PY'
from pathlib import Path
import os
import sys

template = Path(sys.argv[1]).read_text(encoding="utf-8")
for key in ("APP_DIR", "APP_USER", "APP_GROUP"):
    template = template.replace(f"@{key}@", os.environ[key])
Path(sys.argv[2]).write_text(template, encoding="utf-8")
PY

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
for attempt in $(seq 1 20); do
  if [[ "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/api/health || true)" == "200" ]]; then
    echo "Servicio instalado y saludable: http://127.0.0.1:8000/api/health"
    exit 0
  fi
  sleep 1
done
echo "El servicio se instaló, pero el endpoint de salud no respondió con HTTP 200." >&2
journalctl -u "${SERVICE_NAME}" -n 40 --no-pager >&2 || true
exit 1
