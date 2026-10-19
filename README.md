# GMV One Meal, One Speech

Portal público para descubrir las exposiciones del equipo, consultar el calendario y compartir conocimiento. Cualquier persona con acceso al portal puede crear una cuenta de miembro, proponer charlas y participar en la conversación; el equipo de administración gestiona la agenda.

## Funcionalidades

- Portada pública con próxima exposición, cuenta atrás y lista de charlas.
- Agenda mensual y fichas con imágenes, enlaces y descripción.
- Registro abierto de cuentas de miembro, protegido con CSRF, contraseñas seguras y límites de solicitudes.
- Propuestas publicadas automáticamente en el siguiente hueco disponible de la agenda (por defecto, el primer martes del mes a las 13:30, Europe/Madrid).
- Sección **Mis charlas** para gestionar próximas exposiciones propias y consultar las realizadas.
- El ponente puede editar o eliminar sus charlas futuras; al eliminar una, todas las charlas programadas posteriores avanzan un turno para cerrar el hueco. También puede ceder su turno intercambiando fechas con la siguiente charla.
- Valoraciones y comentarios durante el periodo entre exposiciones; el administrador marca la charla como realizada para abrirlo y solo el ponente puede responder una vez a cada comentario.
- Estadísticas públicas de charlas impartidas y más valoradas.
- Agenda mensual configurable desde Administración: se pueden habilitar ocurrencias concretas de cada día de la semana y fijar una hora predeterminada. Al cambiar las reglas, todas las charlas futuras se recolocan manteniendo su orden.
- Panel para administrar miembros, contraseñas temporales y fechas. El primer administrador creado con `flask create-admin` es el superadministrador protegido; al actualizar una base existente, se asigna al administrador activo más antiguo.

## Requisitos

- Python 3.12 o superior.
- Para el despliegue del servicio: Linux con systemd.
- Para ejecutar las pruebas E2E localmente: Chromium de Playwright.

## Desarrollo local

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
flask --app wsgi:app db upgrade
flask --app wsgi:app create-admin
flask --app wsgi:app run
```

La aplicación estará disponible en `http://127.0.0.1:5000`. `flask create-admin` solicita el nombre de usuario y la contraseña de forma interactiva. No existe registro público de administradores.

`wsgi.py` carga `.env` automáticamente tanto para Flask como para Gunicorn. `.env` está excluido de Git; `.env.example` solo contiene claves de desarrollo y no debe copiarse a producción. Para un entorno real, configura claves aleatorias, distintas y persistentes.

Para una configuración local alternativa, define `DATABASE_URL` y `UPLOAD_FOLDER`. La base SQLite y las imágenes deben quedar fuera del control de versiones.

## Pruebas y estilo

```bash
source .venv/bin/activate
playwright install chromium
pytest -q
ruff check app tests wsgi.py
```

La suite contiene pruebas unitarias de programación, pruebas de integración con el cliente de Flask y un flujo E2E de navegador. GitHub Actions ejecuta las pruebas y un smoke test de despliegue en cada push a cualquier rama y en pull requests a `main`; este último aplica migraciones, inicia Redis y Gunicorn con configuración de producción y exige HTTP 200 en `/api/health`.

## Despliegue y actualización

`deploy.sh` detecta el checkout a partir de la ubicación del propio script y guarda la base SQLite y las imágenes en `<checkout>/instance/`. La unidad systemd usa ese mismo checkout como `WorkingDirectory` y solo permite escribir en su `instance/`. Ejecuta el despliegue con `sudo` desde una cuenta no-root que sea propietaria del checkout y vaya a ejecutar el proceso; no instales el repositorio bajo `/root`. Si el servicio ya existe, el script no lo reconfigura. El despliegue incluido usa Redis local: instala y activa `redis-server` antes de ejecutarlo (`sudo apt install redis-server && sudo systemctl enable --now redis-server`).

En producción, `deploy.sh` crea y protege `/etc/default/gmv-one-meal-one-speech` con claves aleatorias y la configuración del servicio; no usa el `.env` local. La base activa es `<checkout>/instance/portal.sqlite3`; las copias de seguridad de `update.sh` quedan en `<checkout>/instance/backups/`. Si el archivo de entorno ya existe y apunta a otra ubicación, el despliegue se detiene para evitar cambiar de base de datos accidentalmente.

Para retirar intencionadamente la instalación anterior y crear una nueva desde cero, ejecuta `sudo ./recreate_deployment.sh --apply` desde el checkout definitivo. También puedes ejecutarlo directamente como `root` indicando `APP_USER=<usuario>`; systemd seguirá ejecutando la aplicación como ese usuario no-root. El checkout debe ser accesible por dicha cuenta y no puede estar bajo `/root`. El script exige confirmar la ruta completa y borra la unidad, el entorno de producción, las bases y archivos de instancia conocidos y los entornos virtuales anterior y nuevo; conserva el código. Genera claves nuevas y una base vacía. No lo uses para una actualización normal.

Después de instalarlo, crea el administrador inicial con el mismo usuario del proceso, cargando la configuración de entorno que instaló el despliegue:

```bash
sudo -u "$USER" bash -c 'set -a; . /etc/default/gmv-one-meal-one-speech; set +a; cd /ruta/al/repositorio; .venv/bin/flask --app wsgi:app create-admin'
```

Ejecuta `update.sh` desde una copia de trabajo limpia. Descarga cambios con `git pull --ff-only`, instala dependencias, guarda una copia de seguridad SQLite, aplica migraciones, reinicia el servicio y exige una respuesta HTTP 200 del endpoint de salud.

El servicio escucha en todas las interfaces IPv4 (`0.0.0.0:8000`), por lo que puede recibir conexiones desde otros equipos si la red y el firewall lo permiten. Para exponerlo fuera de una red de confianza, usa HTTPS mediante un proxy inverso; en producción las cookies requieren HTTPS (`COOKIE_SECURE=1`).

## Datos y configuración

- Variables requeridas en producción: `SECRET_KEY`, `JWT_SECRET_KEY`, `DATABASE_URL`, `DATABASE_PATH` y `UPLOAD_FOLDER`. Las dos claves deben ser distintas y contener al menos 32 bytes aleatorios.
- `COOKIE_SECURE=1` es obligatorio en producción. Los límites de peticiones usan `RATELIMIT_STORAGE_URI`; el despliegue configura `redis://127.0.0.1:6379/0`, compartido por los dos workers de Gunicorn. No uses `memory://` en producción. Si se utiliza Redis remoto, configura su URI y adapta la dependencia local de Redis del servicio systemd.
- Hay un límite general de 300 solicitudes por minuto y dirección IP; el acceso tiene además límites para intentos de autenticación y registro. Los límites no se desactivan si Redis no está disponible.
- Si hay un proxy inverso delante de Gunicorn, configura `TRUSTED_PROXY_COUNT` con el número exacto de proxies que gestionan `X-Forwarded-For`; el proxy de borde debe reemplazar/normalizar esa cabecera. El valor predeterminado `0` no confía en cabeceras reenviadas.
- Los administradores normales pueden ascender miembros, pero no degradar ni desactivar a otros administradores, ni restablecer contraseñas desde el panel. Solo el superadministrador puede gestionar contraseñas ajenas o retirar permisos de otros administradores; su propia cuenta no se puede degradar ni desactivar.
- Las cuentas, votos y comentarios se guardan en SQLite. Las contraseñas se almacenan con hash y las claves temporales solo se muestran al generarse.
- La hora se almacena en UTC y se presenta en `Europe/Madrid`.

## Identidad visual

La interfaz toma como referencia la identidad pública de GMV. El logotipo de `app/static/img/gmv-logo.svg` es una marca de GMV y no está cubierto por la licencia Apache 2.0 de este repositorio; consulta `NOTICE`.
