"""
Monitor de Telão - CDP edition
Controla o Edge via Chrome DevTools Protocol sem precisar de driver externo.
"""

import os
import sys
import time
import json
import logging
import signal
import subprocess
import glob
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
SUBMIT_SELECTOR   = os.getenv("SUBMIT_SELECTOR", '#warn-me')
SUCCESS_INDICATOR = os.getenv("SUCCESS_INDICATOR", "")
CHECK_INTERVAL    = int(os.getenv("CHECK_INTERVAL_SEC", "30"))
LOGIN_TIMEOUT     = float(os.getenv("LOGIN_TIMEOUT_MS", "15000")) / 1000
NAV_TIMEOUT       = float(os.getenv("NAV_TIMEOUT_MS", "30000")) / 1000
CDP_PORT          = int(os.getenv("CDP_PORT", "9222"))
PROFILE_DIR       = os.getenv("USER_DATA_DIR", str(Path(__file__).parent / ".edge_profile"))

if not TARGET_URL:
    log.error("TARGET_URL não configurada. Edite o arquivo .env antes de continuar.")
    sys.exit(1)

# ── Sinal de parada ──────────────────────────────────────────────────────────
_running = True
_edge_proc = None
_last_recovery = 0
RECOVERY_COOLDOWN = 60  # segundos entre tentativas de recuperação

def _stop(signum, frame):
    global _running
    log.info("Encerrando monitor...")
    _running = False

signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)

# ── Localiza Edge ─────────────────────────────────────────────────────────────
def find_edge():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    candidates += glob.glob(r"C:\Program Files*\Microsoft\Edge\Application\msedge.exe")
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("msedge.exe não encontrado.")

# ── CDP HTTP helpers ──────────────────────────────────────────────────────────
def cdp_get(path):
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}{path}", timeout=5) as r:
        return json.loads(r.read())

def get_page_tab():
    """Retorna a primeira aba do tipo 'page'."""
    try:
        for tab in cdp_get("/json/list"):
            if tab.get("type") == "page":
                return tab
    except Exception:
        pass
    return None

# ── CDP WebSocket via websocket-client ────────────────────────────────────────
def cdp_ws_exec(ws_url, commands):
    """
    Executa uma lista de comandos CDP via WebSocket.
    commands: lista de (method, params_dict)
    """
    import websocket  # websocket-client
    ws = websocket.create_connection(ws_url, timeout=10)
    try:
        for i, (method, params) in enumerate(commands, start=1):
            msg = json.dumps({"id": i, "method": method, "params": params})
            ws.send(msg)
            # Aguarda resposta com o id correspondente
            deadline = time.time() + 10
            while time.time() < deadline:
                raw = ws.recv()
                data = json.loads(raw)
                if data.get("id") == i:
                    if "error" in data:
                        log.warning("CDP erro id=%d: %s", i, data["error"])
                    break
    finally:
        ws.close()

def js(ws_url, expression):
    """Executa JavaScript na aba."""
    log.debug("JS: %s", expression[:80])
    cdp_ws_exec(ws_url, [
        ("Runtime.evaluate", {"expression": expression, "awaitPromise": False})
    ])

def navigate_cdp(ws_url, url):
    """Navega para uma URL via CDP."""
    log.info("Navegando para: %s", url)
    cdp_ws_exec(ws_url, [("Page.navigate", {"url": url})])
    time.sleep(3)

# ── Edge: inicia e verifica ───────────────────────────────────────────────────
def kill_edge():
    """Mata todos os processos msedge.exe existentes."""
    try:
        subprocess.call(["taskkill", "/f", "/im", "msedge.exe"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        log.info("Processos Edge encerrados.")
    except Exception:
        pass

def start_edge():
    global _edge_proc
    kill_edge()
    edge_exe = find_edge()
    log.info("Iniciando Edge: %s", edge_exe)
    _edge_proc = subprocess.Popen([
        edge_exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--start-fullscreen",
        "--disable-infobars",
        "--noerrdialogs",
        "--disable-session-crashed-bubble",
        "--no-restore-last-session",
        "--restore-last-session=0",
        "--new-window",
        "--kiosk",
        LOGIN_URL,
    ])
    for _ in range(20):
        time.sleep(1)
        try:
            cdp_get("/json/version")
            log.info("Edge iniciado e CDP disponível.")
            return True
        except Exception:
            pass
    log.error("Edge não respondeu ao CDP.")
    return False

def is_edge_running():
    if _edge_proc and _edge_proc.poll() is None:
        try:
            cdp_get("/json/version")
            return True
        except Exception:
            pass
    return False

# ── Login ─────────────────────────────────────────────────────────────────────
def do_login():
    log.info("Iniciando login em: %s", LOGIN_URL)
    tab = get_page_tab()
    if not tab:
        log.error("Nenhuma aba disponível.")
        return False

    ws_url = tab["webSocketDebuggerUrl"]

    # Navega para o login
    navigate_cdp(ws_url, LOGIN_URL)
    time.sleep(2)

    # Recarrega o ws_url (pode mudar após navegação)
    tab = get_page_tab()
    if not tab:
        log.error("Aba sumiu após navegação.")
        return False
    ws_url = tab["webSocketDebuggerUrl"]

    def esc(s):
        return s.replace("\\", "\\\\").replace("'", "\\'")

    # Preenche campos e clica
    try:
        js(ws_url, f"document.querySelector('{USERNAME_SELECTOR}').value = '{esc(USERNAME)}'")
        time.sleep(0.5)
        js(ws_url, f"document.querySelector('{PASSWORD_SELECTOR}').value = '{esc(PASSWORD)}'")
        time.sleep(0.5)
        js(ws_url, f"document.querySelector('{SUBMIT_SELECTOR}').click()")
        time.sleep(3)
    except Exception as exc:
        log.error("Erro durante login: %s", exc)
        return False

    tab = get_page_tab()
    url_after = tab.get("url", "") if tab else ""
    log.info("URL após login: %s", url_after)
    return True

# ── Verificação ───────────────────────────────────────────────────────────────
def current_url_matches():
    tab = get_page_tab()
    if not tab:
        return False
    url = tab.get("url", "")
    match = url.startswith(TARGET_URL)
    if not match and url:
        log.warning("URL incorreta: '%s'", url)
    return match

def recover():
    global _last_recovery
    now = time.time()
    if now - _last_recovery < RECOVERY_COOLDOWN:
        log.debug("Aguardando cooldown de recuperação...")
        return False
    _last_recovery = now

    log.info("=== Iniciando recuperação ===")

    # Tenta ir direto para o alvo (sem login)
    tab = get_page_tab()
    if tab:
        navigate_cdp(tab["webSocketDebuggerUrl"], TARGET_URL)
        if current_url_matches():
            log.info("Recuperado sem login.")
            return True

    # Precisa de login
    do_login()
    time.sleep(2)
    tab = get_page_tab()
    if tab:
        navigate_cdp(tab["webSocketDebuggerUrl"], TARGET_URL)
        time.sleep(2)

    success = current_url_matches()
    log.info("=== Recuperação %s ===", "OK" if success else "falhou")
    return success

# ── Loop principal ────────────────────────────────────────────────────────────
def run_monitor():
    log.info("Iniciando monitor | TARGET=%s | intervalo=%ds", TARGET_URL, CHECK_INTERVAL)

    if not is_edge_running():
        if not start_edge():
            sys.exit(1)
    else:
        log.info("Edge já em execução.")

    time.sleep(2)
    if not current_url_matches():
        recover()
    else:
        log.info("Telão já na página correta.")

    while _running:
        time.sleep(CHECK_INTERVAL)
        if not _running:
            break
        try:
            if not is_edge_running():
                log.warning("Edge fechou. Reiniciando...")
                start_edge()
                time.sleep(2)
                recover()
                continue

            if not current_url_matches():
                recover()
            else:
                log.debug("OK")
        except Exception as exc:
            log.error("Erro inesperado: %s", exc)

    log.info("Monitor encerrado.")

if __name__ == "__main__":
    run_monitor()
