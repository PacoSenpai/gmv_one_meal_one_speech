from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from werkzeug.serving import make_server

from app.extensions import db
from app.models import Talk, User, utc_now


def test_public_member_can_register_and_publish_a_talk_in_a_real_browser(app):
    playwright_module = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error as PlaywrightError

    server = make_server("127.0.0.1", 0, app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    playwright = playwright_module.sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.skip(f"Chromium no está instalado: {error}")
        page = browser.new_page()
        page.goto(f"{base_url}/registro")
        page.get_by_label("Nombre de usuario").fill("curious_member")
        page.get_by_label("Nombre completo").fill("Curious Member")
        page.get_by_label("Contraseña").fill("e2e-member-secure-password")
        page.get_by_role("button", name="Crear mi cuenta").click()
        page.get_by_role("link", name="Proponer charla").click()
        page.get_by_label("Título de la exposición").fill("A real browser journey through space")
        page.get_by_label("Descripción").fill(
            "Una descripción suficientemente larga para superar la validación y explicar el tema."
            " La exposición se comparte durante la comida."
        )
        page.get_by_label("Recursos").fill("Archivo de referencia | https://example.org/space")
        page.get_by_role("button", name="Enviar propuesta").click()
        page.get_by_role("heading", name="A real browser journey through space").wait_for()
        assert "curious_member" in page.locator(".speaker-card").inner_text()
        browser.close()
    finally:
        playwright.stop()
        server.shutdown()
        server_thread.join(timeout=3)


def test_members_can_rate_and_the_speaker_can_reply_in_a_real_browser(app):
    playwright_module = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error as PlaywrightError

    with app.app_context():
        speaker = User(username="speaker", full_name="Speaker", role="member")
        speaker.set_password("speaker-secure-password")
        listener = User(username="listener", full_name="Listener", role="member")
        listener.set_password("listener-secure-password")
        db.session.add_all([speaker, listener])
        db.session.flush()
        completed_at = utc_now() - timedelta(minutes=10)
        talk = Talk(
            title="A browser-tested conversation",
            description="Una descripción suficientemente extensa para una charla ya realizada.",
            event_at=completed_at - timedelta(hours=1),
            completed_at=completed_at,
            status="completed",
            speaker_id=speaker.id,
        )
        following = Talk(
            title="The next monthly talk",
            description="Otra descripción suficientemente larga para validar la charla futura.",
            event_at=utc_now() + timedelta(days=5),
            status="scheduled",
            speaker_id=listener.id,
        )
        db.session.add_all([talk, following])
        db.session.commit()
        talk_id = talk.id

    server = make_server("127.0.0.1", 0, app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    playwright = playwright_module.sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.skip(f"Chromium no está instalado: {error}")
        page = browser.new_page()
        page.goto(f"{base_url}/login")
        page.get_by_label("Nombre de usuario").fill("listener")
        page.get_by_label("Contraseña").fill("listener-secure-password")
        page.get_by_role("button", name="Entrar").click()
        page.goto(f"{base_url}/charlas/{talk_id}")
        page.get_by_role("button", name="Me ha gustado").click()
        page.get_by_label("Escribe un comentario").fill("Me llevo una idea nueva.")
        page.get_by_role("button", name="Publicar comentario").click()
        page.get_by_text("Me llevo una idea nueva.").wait_for()
        page.get_by_role("button", name="Salir").click()

        page.goto(f"{base_url}/login")
        page.get_by_label("Nombre de usuario").fill("speaker")
        page.get_by_label("Contraseña").fill("speaker-secure-password")
        page.get_by_role("button", name="Entrar").click()
        page.goto(f"{base_url}/charlas/{talk_id}")
        page.get_by_label("Responder a Listener").fill("Gracias por escuchar y compartir.")
        page.get_by_role("button", name="Responder").click()
        page.get_by_text("Gracias por escuchar y compartir.").wait_for()
        browser.close()
    finally:
        playwright.stop()
        server.shutdown()
        server_thread.join(timeout=3)
