#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="gmv-one-meal-one-speech.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
ENV_FILE="/etc/default/gmv-one-meal-one-speech"
STATE_DIR="/var/lib/gmv-one-meal-one-speech"
LEGACY_ROOT_INSTANCE="/root/gmv_one_meal_one_speech/instance"
LEGACY_ROOT_VENV="/root/gmv_one_meal_one_speech/.venv"

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
APP_DIR="$(git -C "$(dirname "${SCRIPT_PATH}")" rev-parse --show-toplevel)"
APP_INSTANCE="${APP_DIR}/instance"
APP_VENV="${APP_DIR}/.venv"

if [[ "${1:-}" != "--apply" || "$#" -ne 1 ]]; then
  echo "Uso: sudo ${SCRIPT_PATH} --apply" >&2
  echo "El script borra la instalación y los datos anteriores antes de reinstalar." >&2
  exit 2
fi

if [[ "${EUID}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Error: sudo es necesario para retirar e instalar el servicio." >&2
    exit 1
  fi
  APP_USER="${APP_USER:-$(id -un)}"
  exec sudo --preserve-env=APP_USER bash "${SCRIPT_PATH}" --apply
fi

APP_USER="${APP_USER:-${SUDO_USER:-}}"
if [[ -z "${APP_USER}" ]]; then
  APP_USER="$(stat -c '%U' "${APP_DIR}")"
fi
if [[ -z "${APP_USER}" ]] || ! id "${APP_USER}" >/dev/null 2>&1 || [[ "${APP_USER}" == "root" ]]; then
  echo "Error: root puede ejecutar el script, pero el servicio requiere un usuario no-root." >&2
  echo "Indica APP_USER=<usuario> y asegúrate de que ese usuario puede acceder al checkout." >&2
  exit 1
fi

if [[ "${APP_DIR}" == /root || "${APP_DIR}" == /root/* ]]; then
  echo "Error: instala el checkout bajo el home de la cuenta de servicio, no bajo /root." >&2
  exit 1
fi
if ! sudo -u "${APP_USER}" test -x "${APP_DIR}" \
  || ! sudo -u "${APP_USER}" test -w "${APP_DIR}"; then
  echo "Error: ${APP_USER} debe poder acceder y escribir en ${APP_DIR}." >&2
  exit 1
fi
for required_file in deploy.sh requirements.txt deploy/gmv-one-meal-one-speech.service.in; do
  if [[ ! -f "${APP_DIR}/${required_file}" ]]; then
    echo "Error: falta ${APP_DIR}/${required_file}; no se ha borrado nada." >&2
    exit 1
  fi
done
for command in systemctl python3 curl; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Error: falta ${command}; no se ha borrado nada." >&2
    exit 1
  fi
done
if ! systemctl show-environment >/dev/null 2>&1; then
  echo "Error: systemd no está disponible; no se ha borrado nada." >&2
  exit 1
fi
if ! systemctl is-active --quiet redis-server.service; then
  echo "Error: Redis debe estar activo antes de reinstalar; no se ha borrado nada." >&2
  exit 1
fi

if systemctl show "${SERVICE_NAME}" --property=LoadState --value >/dev/null 2>&1; then
  fragment="$(systemctl show "${SERVICE_NAME}" --property=FragmentPath --value 2>/dev/null || true)"
  if [[ -n "${fragment}" && "${fragment}" != "${SERVICE_PATH}" ]]; then
    echo "Error: ${SERVICE_NAME} está definido en ${fragment}, no en ${SERVICE_PATH}." >&2
    echo "No se ha borrado nada; revisa esa unidad antes de continuar." >&2
    exit 1
  fi
fi

for directory in "${APP_INSTANCE}" "${APP_VENV}" "${STATE_DIR}"; do
  if [[ -L "${directory}" ]]; then
    echo "Error: ${directory} es un enlace simbólico; no se ha borrado nada." >&2
    exit 1
  fi
  if [[ -e "${directory}" && ! -d "${directory}" ]]; then
    echo "Error: ${directory} existe, pero no es un directorio; no se ha borrado nada." >&2
    exit 1
  fi
done
if [[ "${LEGACY_ROOT_INSTANCE}" != "${APP_INSTANCE}" && -L "${LEGACY_ROOT_INSTANCE}" ]]; then
  echo "Error: ${LEGACY_ROOT_INSTANCE} es un enlace simbólico; no se ha borrado nada." >&2
  exit 1
fi
if [[ "${LEGACY_ROOT_INSTANCE}" != "${APP_INSTANCE}" \
  && -e "${LEGACY_ROOT_INSTANCE}" && ! -d "${LEGACY_ROOT_INSTANCE}" ]]; then
  echo "Error: ${LEGACY_ROOT_INSTANCE} no es un directorio; no se ha borrado nada." >&2
  exit 1
fi
if [[ "${LEGACY_ROOT_VENV}" != "${APP_VENV}" && -L "${LEGACY_ROOT_VENV}" ]]; then
  echo "Error: ${LEGACY_ROOT_VENV} es un enlace simbólico; no se ha borrado nada." >&2
  exit 1
fi
if [[ "${LEGACY_ROOT_VENV}" != "${APP_VENV}" \
  && -e "${LEGACY_ROOT_VENV}" && ! -d "${LEGACY_ROOT_VENV}" ]]; then
  echo "Error: ${LEGACY_ROOT_VENV} no es un directorio; no se ha borrado nada." >&2
  exit 1
fi
if [[ -L "${ENV_FILE}" || -L "${SERVICE_PATH}" ]]; then
  echo "Error: el archivo de entorno o la unidad es un enlace simbólico; no se ha borrado nada." >&2
  exit 1
fi
if [[ -e "${ENV_FILE}" && ! -f "${ENV_FILE}" ]] \
  || [[ -e "${SERVICE_PATH}" && ! -f "${SERVICE_PATH}" ]]; then
  echo "Error: el archivo de entorno o la unidad no es un archivo normal; no se ha borrado nada." >&2
  exit 1
fi

printf '\nSe eliminarán permanentemente:\n'
printf '  - unidad y configuración: %s, %s y sus drop-ins\n' "${SERVICE_PATH}" "${ENV_FILE}"
printf '  - datos antiguos: %s\n' "${STATE_DIR}"
printf '  - estado del checkout: %s y %s\n' "${APP_INSTANCE}" "${APP_VENV}"
if [[ "${LEGACY_ROOT_INSTANCE}" != "${APP_INSTANCE}" && -e "${LEGACY_ROOT_INSTANCE}" ]]; then
  printf '  - instancia del checkout antiguo: %s\n' "${LEGACY_ROOT_INSTANCE}"
fi
if [[ "${LEGACY_ROOT_VENV}" != "${APP_VENV}" && -e "${LEGACY_ROOT_VENV}" ]]; then
  printf '  - entorno virtual del checkout antiguo: %s\n' "${LEGACY_ROOT_VENV}"
fi
printf '\n%s\n' \
  'Se conservará el código del repositorio.' \
  'La nueva instalación generará claves y una base vacía.'
printf 'Para continuar, escribe exactamente: RECREAR %s\n> ' "${APP_DIR}"
if ! IFS= read -r confirmation || [[ "${confirmation}" != "RECREAR ${APP_DIR}" ]]; then
  echo "Cancelado; no se ha borrado nada."
  exit 1
fi

systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
if systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo "Error: no se pudo detener el servicio; no se ha borrado ningún dato." >&2
  exit 1
fi
systemctl disable "${SERVICE_NAME}" >/dev/null 2>&1 || true

rm -f -- "${SERVICE_PATH}"
rm -rf -- "/etc/systemd/system/${SERVICE_NAME}.d"
rm -f -- "/etc/systemd/system/multi-user.target.wants/${SERVICE_NAME}"
rm -f -- "${ENV_FILE}"
systemctl daemon-reload
systemctl reset-failed "${SERVICE_NAME}" >/dev/null 2>&1 || true

for directory in "${APP_INSTANCE}" "${APP_VENV}" "${STATE_DIR}"; do
  if [[ -d "${directory}" ]]; then
    rm -rf -- "${directory}"
  fi
done
if [[ "${LEGACY_ROOT_INSTANCE}" != "${APP_INSTANCE}" && -d "${LEGACY_ROOT_INSTANCE}" ]]; then
  rm -rf -- "${LEGACY_ROOT_INSTANCE}"
fi
if [[ "${LEGACY_ROOT_VENV}" != "${APP_VENV}" && -d "${LEGACY_ROOT_VENV}" ]]; then
  rm -rf -- "${LEGACY_ROOT_VENV}"
fi

echo "Instalación anterior retirada. Creando una instalación limpia desde ${APP_DIR}..."
APP_USER="${APP_USER}" bash "${APP_DIR}/deploy.sh"
