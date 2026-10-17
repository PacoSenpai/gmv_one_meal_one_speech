# GMV One Meal, One Speech

Portal público para descubrir las exposiciones del equipo, consultar el calendario y compartir conocimiento. Las cuentas se crean por invitación; los miembros pueden proponer charlas y el equipo de administración gestiona la agenda.

## Funcionalidades

- Portada pública con próxima exposición, cuenta atrás y lista de charlas.
- Agenda mensual y fichas con imágenes, enlaces y descripción.
- Registro por invitación de un solo uso, cuentas de miembro y administración.
- Propuestas publicadas automáticamente en el siguiente primer martes libre, a las 13:30 (Europe/Madrid).
- Sección **Mis charlas** para gestionar próximas exposiciones propias y consultar las realizadas.
- El ponente puede editar o eliminar sus charlas futuras; al eliminar una, todas las charlas programadas posteriores avanzan un turno para cerrar el hueco. También puede ceder su turno intercambiando fechas con la siguiente charla.
- Valoraciones y comentarios durante el periodo entre exposiciones; el administrador marca la charla como realizada para abrirlo y solo el ponente puede responder una vez a cada comentario.
- Estadísticas públicas de charlas impartidas y más valoradas.
- Panel para administrar invitaciones, miembros, contraseñas temporales y fechas.

## Requisitos

- Python 3.12 o superior.
- Para el despliegue del servicio: Linux con systemd.
- Para ejecutar las pruebas E2E localmente: Chromium de Playwright.

## Desarrollo local

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export SECRET_KEY="clave-local-cambia-esto"
export JWT_SECRET_KEY="otra-clave-local-cambia-esto"
export FLASK_APP=wsgi:app
flask db upgrade
flask create-admin
flask run
```

La aplicación estará disponible en `http://127.0.0.1:5000`. `flask create-admin` solicita el nombre de usuario y la contraseña de forma interactiva. No existe registro público de administradores.

Para una configuración local alternativa, define `DATABASE_URL` y `UPLOAD_FOLDER`. La base SQLite y las imágenes deben quedar fuera del control de versiones.

## Pruebas y estilo

```bash
source .venv/bin/activate
playwright install chromium
pytest -q
ruff check app tests wsgi.py
```

La suite contiene pruebas unitarias de programación, pruebas de integración con el cliente de Flask y un flujo E2E de navegador. GitHub Actions ejecuta pruebas en cada push a cualquier rama y en pull requests a `main`; un merge a `main` vuelve a ejecutarlas mediante el evento `push`.

## Despliegue y actualización

`deploy.sh` crea el entorno virtual, configura la base de datos y las imágenes en `/var/lib/gmv-one-meal-one-speech`, instala el servicio systemd y comprueba `/api/health`. Debe ejecutarse con `sudo` desde la cuenta que será propietaria del proceso. Si el servicio ya existe, el script informa y no modifica nada.

Después de instalarlo, crea el administrador inicial con el mismo usuario del proceso, cargando la configuración de entorno que instaló el despliegue:

```bash
sudo -u "$USER" bash -c 'set -a; . /etc/default/gmv-one-meal-one-speech; set +a; cd /ruta/al/repositorio; .venv/bin/flask --app wsgi:app create-admin'
```

Ejecuta `update.sh` desde una copia de trabajo limpia. Descarga cambios con `git pull --ff-only`, instala dependencias, guarda una copia de seguridad SQLite, aplica migraciones, reinicia el servicio y exige una respuesta HTTP 200 del endpoint de salud.

El servicio escucha en `127.0.0.1:8000`. Configura el acceso HTTPS y el proxy de acuerdo con la infraestructura donde se ejecute.

## Datos y configuración

- Variables requeridas en producción: `SECRET_KEY`, `JWT_SECRET_KEY`, `DATABASE_URL`, `DATABASE_PATH` y `UPLOAD_FOLDER`.
- Las cookies seguras se activan con `COOKIE_SECURE=1`.
- Los códigos de invitación, votos y comentarios se guardan en SQLite. Las contraseñas se almacenan con hash; los códigos de invitación se guardan como hash y las claves temporales solo se muestran al generarse.
- La hora se almacena en UTC y se presenta en `Europe/Madrid`.

## Identidad visual

La interfaz toma como referencia la identidad pública de GMV. El logotipo de `app/static/img/gmv-logo.svg` es una marca de GMV y no está cubierto por la licencia Apache 2.0 de este repositorio; consulta `NOTICE`.
