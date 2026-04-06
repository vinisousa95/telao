"""
Monitor de Telão - Verifica se o browser está na página correta e
faz login automático se necessário.

Uso: python monitor.py
Configuração: copie .env.example para .env e preencha as variáveis.
"""

import os
import sys
import time
import logging
import signal
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Configuração de logging ──────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"monitor_{datetime.now():%Y%m%d}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("telao-monitor")

# ── Carrega variáveis de ambiente ────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

TARGET_URL        = os.getenv("TARGET_URL", "")
LOGIN_URL         = os.getenv("LOGIN_URL", TARGET_URL)
USERNAME          = os.getenv("TELAO_USERNAME", "")
PASSWORD          = os.getenv("TELAO_PASSWORD", "")
USERNAME_SELECTOR = os.getenv("USERNAME_SELECTOR", 'input[name="username"], input[type="text"], #username')
PASSWORD_SELECTOR = os.getenv("PASSWORD_SELECTOR", 'input[name="password"], input[type="password"], #password')
SUBMIT_SELECTOR   = os.getenv("SUBMIT_SELECTOR", 'button[type="submit"], input[type="submit"]')
SUCCESS_INDICATOR = os.getenv("SUCCESS_INDICATOR", "")   # seletor CSS ou texto que confirma login OK
CHECK_INTERVAL    = int(os.getenv("CHECK_INTERVAL_SEC", "30"))
LOGIN_TIMEOUT     = int(os.getenv("LOGIN_TIMEOUT_MS", "15000"))
NAV_TIMEOUT       = int(os.getenv("NAV_TIMEOUT_MS", "30000"))
HEADLESS          = os.getenv("HEADLESS", "false").lower() == "true"
USER_DATA_DIR     = os.getenv("USER_DATA_DIR", str(Path(__file__).parent / ".browser_profile"))
CDP_URL           = os.getenv("CDP_URL", "")   # ex: http://localhost:9222  (deixe vazio para lançar browser novo)

# ── Validação básica ─────────────────────────────────────────────────────────
if not TARGET_URL:
    log.error("TARGET_URL não configurada. Edite o arquivo .env antes de continuar.")
    sys.exit(1)

# ── Sinal de parada ──────────────────────────────────────────────────────────
_running = True

def _stop(signum, frame):
    global _running
    log.info("Sinal de parada recebido. Encerrando monitoramento...")
    _running = False

signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)

# ── Helpers ──────────────────────────────────────────────────────────────────

def current_url_matches(page) -> bool:
    """Retorna True se a URL atual começa com TARGET_URL."""
    url = page.url
    match = url.startswith(TARGET_URL)
    if not match:
        log.warning("URL incorreta: '%s' (esperado começar com '%s')", url, TARGET_URL)
    return match


def do_login(page) -> bool:
    """Navega até LOGIN_URL, preenche credenciais e submete o formulário.
    Retorna True em caso de sucesso."""
    log.info("Navegando para a página de login: %s", LOGIN_URL)
    try:
        page.goto(LOGIN_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
    except PlaywrightTimeout:
        log.error("Timeout ao carregar a página de login.")
        return False

    # Preenche usuário
    try:
        page.fill(USERNAME_SELECTOR, USERNAME, timeout=LOGIN_TIMEOUT)
    except Exception as exc:
        log.error("Não encontrou o campo de usuário (%s): %s", USERNAME_SELECTOR, exc)
        return False

    # Preenche senha
    try:
        page.fill(PASSWORD_SELECTOR, PASSWORD, timeout=LOGIN_TIMEOUT)
    except Exception as exc:
        log.error("Não encontrou o campo de senha (%s): %s", PASSWORD_SELECTOR, exc)
        return False

    # Submete
    try:
        page.click(SUBMIT_SELECTOR, timeout=LOGIN_TIMEOUT)
    except Exception as exc:
        log.error("Não encontrou o botão de submit (%s): %s", SUBMIT_SELECTOR, exc)
        return False

    # Aguarda navegação
    try:
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
    except PlaywrightTimeout:
        log.warning("Timeout aguardando carregamento após login.")

    # Verifica indicador de sucesso opcional
    if SUCCESS_INDICATOR:
        try:
            page.wait_for_selector(SUCCESS_INDICATOR, timeout=LOGIN_TIMEOUT)
            log.info("Login confirmado pelo indicador de sucesso.")
        except PlaywrightTimeout:
            log.error("Indicador de sucesso não encontrado após login ('%s').", SUCCESS_INDICATOR)
            return False

    log.info("Login realizado. URL atual: %s", page.url)
    return True


def navigate_to_target(page) -> bool:
    """Navega para TARGET_URL."""
    log.info("Navegando para a página alvo: %s", TARGET_URL)
    try:
        page.goto(TARGET_URL, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        log.info("Navegação concluída. URL: %s", page.url)
        return True
    except PlaywrightTimeout:
        log.error("Timeout ao navegar para '%s'.", TARGET_URL)
        return False
    except Exception as exc:
        log.error("Erro ao navegar para '%s': %s", TARGET_URL, exc)
        return False


def recover(page):
    """Tenta recuperar o telão: faz login (se necessário) e navega para a URL alvo."""
    log.info("=== Iniciando recuperação do telão ===")

    # Se já estamos em alguma URL do sistema, tenta ir direto para o alvo
    if navigate_to_target(page):
        if current_url_matches(page):
            log.info("Recuperação bem-sucedida sem login.")
            return True

    # Precisa de login
    if not do_login(page):
        log.error("Falha no login. Será tentado novamente no próximo ciclo.")
        return False

    # Após login, garante que está na página alvo
    if not current_url_matches(page):
        navigate_to_target(page)

    success = current_url_matches(page)
    if success:
        log.info("=== Telão recuperado com sucesso ===")
    else:
        log.error("=== Não foi possível recuperar o telão neste ciclo ===")
    return success


# ── Loop principal ───────────────────────────────────────────────────────────

def run_monitor():
    log.info("Iniciando monitor de telão | TARGET_URL=%s | intervalo=%ds", TARGET_URL, CHECK_INTERVAL)

    with sync_playwright() as pw:
        # ── Conecta a browser existente via CDP ou lança um novo ──────────────
        if CDP_URL:
            log.info("Conectando ao browser existente via CDP: %s", CDP_URL)
            browser = pw.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            log.info("Lançando browser Chromium (persistente, headless=%s)", HEADLESS)
            context = pw.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=HEADLESS,
                no_viewport=True,
                args=["--start-fullscreen", "--disable-infobars", "--noerrdialogs",
                      "--disable-session-crashed-bubble", "--kiosk"],
            )

        # ── Garante que existe ao menos uma aba ──────────────────────────────
        page = context.pages[0] if context.pages else context.new_page()

        # Primeira navegação
        if not current_url_matches(page):
            recover(page)
        else:
            log.info("Telão já está na página correta: %s", page.url)

        # ── Loop de monitoramento ─────────────────────────────────────────────
        while _running:
            time.sleep(CHECK_INTERVAL)
            if not _running:
                break

            try:
                if not current_url_matches(page):
                    recover(page)
                else:
                    log.debug("OK | %s", page.url)
            except Exception as exc:
                log.error("Erro inesperado durante verificação: %s", exc)
                # Tenta reabrir a aba
                try:
                    page = context.new_page()
                    recover(page)
                except Exception as exc2:
                    log.error("Falha ao reabrir aba: %s", exc2)

        log.info("Monitor encerrado.")
        try:
            context.close()
        except Exception:
            pass


if __name__ == "__main__":
    run_monitor()
