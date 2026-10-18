#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
APP_DIR="$(git -C "$(dirname "${SCRIPT_PATH}")" rev-parse --show-toplevel)"
SERVICE_NAME="gmv-one-meal-one-speech.service"
ENV_FILE="/etc/default/gmv-one-meal-one-speech"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Error: no existe ${ENV_FILE}; ejecuta deploy.sh primero." >&2
  exit 1
fi
if [[ -n "$(git -C "${APP_DIR}" status --porcelain --untracked-files=no)" ]]; then
  echo "Error: hay cambios locales versionados. Confírmalos o guárdalos antes de actualizar." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

if ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl es necesario para comprobar el endpoint de salud." >&2
  exit 1
fi
if ! sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo "Error: el servicio ${SERVICE_NAME} no está activo." >&2
  exit 1
fi

EXPECTED_DB_PATH="${APP_DIR}/instance/portal.sqlite3"
EXPECTED_UPLOAD_FOLDER="${APP_DIR}/instance/uploads"
if [[ "${DATABASE_URL:-}" != "sqlite:///portal.sqlite3" \
  || "${DATABASE_PATH:-}" != "${EXPECTED_DB_PATH}" \
  || "${UPLOAD_FOLDER:-}" != "${EXPECTED_UPLOAD_FOLDER}" ]]; then
  echo "Error: la configuración del servicio no apunta a instance/ del checkout actual." >&2
  echo "Vuelve a configurar el despliegue antes de actualizar; no se ha modificado nada." >&2
  exit 1
fi

SERVICE_WORKING_DIR="$(sudo systemctl show "${SERVICE_NAME}" --property=WorkingDirectory --value)"
if [[ "$(realpath -m "${SERVICE_WORKING_DIR}")" != "$(realpath -m "${APP_DIR}")" ]]; then
  echo "Error: systemd usa ${SERVICE_WORKING_DIR}, no el checkout ${APP_DIR}." >&2
  echo "Ejecuta update.sh desde el checkout configurado en el servicio." >&2
  exit 1
fi

RATE_LIMIT_STORAGE_URI="${RATELIMIT_STORAGE_URI:-redis://127.0.0.1:6379/0}"
if [[ "${RATE_LIMIT_STORAGE_URI}" == redis://127.0.0.1:6379/* ]] \
  && ! sudo systemctl is-active --quiet redis-server.service; then
  echo "Error: Redis local no está activo; los límites de peticiones no se pueden compartir." >&2
  exit 1
fi

DB_PATH="${DATABASE_PATH:-}"
if [[ -n "${DB_PATH}" && -f "${DB_PATH}" ]]; then
  BACKUP_DIR="$(dirname "${DB_PATH}")/backups"
  mkdir -p "${BACKUP_DIR}"
  BACKUP_PATH="${BACKUP_DIR}/portal-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
  sudo systemctl stop "${SERVICE_NAME}"
  trap 'sudo systemctl start "${SERVICE_NAME}" >/dev/null 2>&1 || true' EXIT
  git -C "${APP_DIR}" pull --ff-only
  "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
  python3 - "${DB_PATH}" "${BACKUP_PATH}" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as destination:
    source.backup(destination)
PY
else
  echo "Error: no se encuentra la base SQLite en DATABASE_PATH." >&2
  exit 1
fi

"${APP_DIR}/.venv/bin/flask" --app wsgi:app db upgrade
sudo systemctl restart "${SERVICE_NAME}"

for attempt in $(seq 1 30); do
  response_code="$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/api/health || true)"
  if [[ "${response_code}" == "200" ]]; then
    trap - EXIT
    echo "Actualización completada; health check HTTP 200. Copia de seguridad: ${BACKUP_PATH}"
    exit 0
  fi
  sleep 1
done

echo "Error: health check no respondió HTTP 200 después de reiniciar el servicio." >&2
sudo systemctl status "${SERVICE_NAME}" --no-pager >&2 || true
exit 1
