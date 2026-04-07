"""
Monitor de Telão - Selenium edition (compatível com Python 3.10 / Windows Store)
Verifica se o browser está na página correta e faz login automático se necessário.
"""

import os
import sys
import time
import logging
import signal
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

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
USERNAME_SELECTOR = os.getenv("USERNAME_SELECTOR", 'input[name="login"]')
PASSWORD_SELECTOR = os.getenv("PASSWORD_SELECTOR", 'input[name="senha"]')
SUBMIT_SELECTOR   = os.getenv("SUBMIT_SELECTOR", 'button[type="submit"]')
SUCCESS_INDICATOR = os.getenv("SUCCESS_INDICATOR", "")
CHECK_INTERVAL    = int(os.getenv("CHECK_INTERVAL_SEC", "30"))
LOGIN_TIMEOUT     = int(os.getenv("LOGIN_TIMEOUT_MS", "15000")) / 1000
NAV_TIMEOUT       = int(os.getenv("NAV_TIMEOUT_MS", "30000")) / 1000
HEADLESS          = os.getenv("HEADLESS", "false").lower() == "true"
BROWSER           = os.getenv("BROWSER", "edge").lower()  # edge ou chrome

if not TARGET_URL:
    log.error("TARGET_URL não configurada. Edite o arquivo .env antes de continuar.")
    sys.exit(1)

# ── Sinal de parada ──────────────────────────────────────────────────────────
_running = True

def _stop(signum, frame):
    global _running
    log.info("Sinal de parada recebido. Encerrando...")
    _running = False

signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)

# ── Cria o driver ────────────────────────────────────────────────────────────
def create_driver():
    from selenium import webdriver
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.chrome.service import Service as ChromeService
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    from webdriver_manager.chrome import ChromeDriverManager

    if BROWSER == "chrome":
        opts = webdriver.ChromeOptions()
        if HEADLESS:
            opts.add_argument("--headless=new")
        opts.add_argument("--start-fullscreen")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--noerrdialogs")
        opts.add_argument("--kiosk")
        driver = webdriver.Chrome(
            service=ChromeService(ChromeDriverManager().install()),
            options=opts,
        )
    else:
        opts = webdriver.EdgeOptions()
        if HEADLESS:
            opts.add_argument("--headless=new")
        opts.add_argument("--start-fullscreen")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--noerrdialogs")
        opts.add_argument("--kiosk")
        driver = webdriver.Edge(
            service=EdgeService(EdgeChromiumDriverManager().install()),
            options=opts,
        )

    driver.set_page_load_timeout(NAV_TIMEOUT)
    return driver


# ── Helpers ──────────────────────────────────────────────────────────────────
def current_url_matches(driver) -> bool:
    url = driver.current_url
    match = url.startswith(TARGET_URL)
    if not match:
        log.warning("URL incorreta: '%s' (esperado começar com '%s')", url, TARGET_URL)
    return match


def find_element(driver, css_selector, timeout=10):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    # Tenta cada seletor separado por vírgula
    for sel in [s.strip() for s in css_selector.split(",")]:
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            return el
        except Exception:
            continue
    return None


def do_login(driver) -> bool:
    log.info("Navegando para login: %s", LOGIN_URL)
    try:
        driver.get(LOGIN_URL)
    except Exception as exc:
        log.error("Erro ao carregar login: %s", exc)
        return False

    user_field = find_element(driver, USERNAME_SELECTOR, LOGIN_TIMEOUT)
    if not user_field:
        log.error("Campo de usuário não encontrado (%s)", USERNAME_SELECTOR)
        return False
    user_field.clear()
    user_field.send_keys(USERNAME)

    pass_field = find_element(driver, PASSWORD_SELECTOR, LOGIN_TIMEOUT)
    if not pass_field:
        log.error("Campo de senha não encontrado (%s)", PASSWORD_SELECTOR)
        return False
    pass_field.clear()
    pass_field.send_keys(PASSWORD)

    submit = find_element(driver, SUBMIT_SELECTOR, LOGIN_TIMEOUT)
    if not submit:
        log.error("Botão de submit não encontrado (%s)", SUBMIT_SELECTOR)
        return False
    submit.click()

    time.sleep(3)  # aguarda redirecionamento

    if SUCCESS_INDICATOR:
        el = find_element(driver, SUCCESS_INDICATOR, LOGIN_TIMEOUT)
        if not el:
            log.error("Indicador de sucesso não encontrado após login.")
            return False

    log.info("Login realizado. URL: %s", driver.current_url)
    return True


def navigate_to_target(driver) -> bool:
    log.info("Navegando para: %s", TARGET_URL)
    try:
        driver.get(TARGET_URL)
        log.info("Navegação OK. URL: %s", driver.current_url)
        return True
    except Exception as exc:
        log.error("Erro ao navegar: %s", exc)
        return False


def recover(driver):
    log.info("=== Iniciando recuperação do telão ===")
    if navigate_to_target(driver) and current_url_matches(driver):
        log.info("Recuperado sem precisar de login.")
        return True

    if not do_login(driver):
        log.error("Falha no login. Tentará novamente no próximo ciclo.")
        return False

    if not current_url_matches(driver):
        navigate_to_target(driver)

    success = current_url_matches(driver)
    log.info("=== Recuperação %s ===", "bem-sucedida" if success else "falhou")
    return success


# ── Loop principal ───────────────────────────────────────────────────────────
def run_monitor():
    log.info("Iniciando monitor | TARGET=%s | intervalo=%ds | browser=%s",
             TARGET_URL, CHECK_INTERVAL, BROWSER)

    driver = create_driver()

    try:
        if not current_url_matches(driver):
            recover(driver)
        else:
            log.info("Telão já está na página correta.")

        while _running:
            time.sleep(CHECK_INTERVAL)
            if not _running:
                break
            try:
                if not current_url_matches(driver):
                    recover(driver)
                else:
                    log.debug("OK | %s", driver.current_url)
            except Exception as exc:
                log.error("Erro inesperado: %s", exc)
                try:
                    driver.quit()
                except Exception:
                    pass
                log.info("Reiniciando browser...")
                driver = create_driver()
                recover(driver)

    finally:
        log.info("Monitor encerrado.")
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run_monitor()
