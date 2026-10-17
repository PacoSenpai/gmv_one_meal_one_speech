#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="gmv-one-meal-one-speech.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
APP_DIR="$(git rev-parse --show-toplevel)"

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

APP_USER="${SUDO_USER:-${APP_USER:-}}"
if [[ -z "${APP_USER}" ]] || ! id "${APP_USER}" >/dev/null 2>&1; then
  echo "Error: ejecuta el script con sudo desde la cuenta que ejecutará la aplicación." >&2
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
STATE_DIR="/var/lib/gmv-one-meal-one-speech"
ENV_FILE="/etc/default/gmv-one-meal-one-speech"
umask 027
install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${STATE_DIR}" "${STATE_DIR}/uploads"

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
DATABASE_URL=sqlite:////var/lib/gmv-one-meal-one-speech/portal.sqlite3
DATABASE_PATH=/var/lib/gmv-one-meal-one-speech/portal.sqlite3
UPLOAD_FOLDER=/var/lib/gmv-one-meal-one-speech/uploads
COOKIE_SECURE=1
APP_ENV=production
RATELIMIT_STORAGE_URI=redis://127.0.0.1:6379/0
TRUSTED_PROXY_COUNT=0
EOF
  chown root:"${APP_GROUP}" "${ENV_FILE}"
  chmod 0640 "${ENV_FILE}"
fi

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
