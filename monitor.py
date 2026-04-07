"""
Monitor de Telão - CDP edition
Controla o Edge via Chrome DevTools Protocol (porta 9222).
Não precisa de driver externo nem download adicional.
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
SUBMIT_SELECTOR   = os.getenv("SUBMIT_SELECTOR", 'button[type="submit"]')
SUCCESS_INDICATOR = os.getenv("SUCCESS_INDICATOR", "")
CHECK_INTERVAL    = int(os.getenv("CHECK_INTERVAL_SEC", "30"))
LOGIN_TIMEOUT     = int(os.getenv("LOGIN_TIMEOUT_MS", "15000")) / 1000
NAV_TIMEOUT       = int(os.getenv("NAV_TIMEOUT_MS", "30000")) / 1000
CDP_PORT          = int(os.getenv("CDP_PORT", "9222"))
PROFILE_DIR       = os.getenv("USER_DATA_DIR", str(Path(__file__).parent / ".edge_profile"))

if not TARGET_URL:
    log.error("TARGET_URL não configurada. Edite o arquivo .env antes de continuar.")
    sys.exit(1)

# ── Sinal de parada ──────────────────────────────────────────────────────────
_running = True
_edge_proc = None

def _stop(signum, frame):
    global _running
    log.info("Encerrando monitor...")
    _running = False

signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)

# ── Localiza o executável do Edge ─────────────────────────────────────────────
def find_edge():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    candidates += glob.glob(r"C:\Program Files*\Microsoft\Edge\Application\msedge.exe")
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("msedge.exe não encontrado. Verifique se o Edge está instalado.")

# ── CDP helpers ───────────────────────────────────────────────────────────────
def cdp_get(path):
    import urllib.request
    url = f"http://127.0.0.1:{CDP_PORT}{path}"
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())

def cdp_ws_send(ws_url, method, params=None):
    """Envia um comando CDP via WebSocket e retorna a resposta."""
    import urllib.request, urllib.parse, hashlib, base64, struct, socket
    # Faz o handshake WebSocket manualmente (sem lib externa)
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query

    key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode()

    sock = socket.create_connection((host, port), timeout=10)
    sock.sendall(handshake)
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)

    # Envia frame WebSocket (texto, sem máscara de servidor→cliente mas com máscara cliente→servidor)
    payload = json.dumps({"id": 1, "method": method, "params": params or {}}).encode()
    length = len(payload)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    frame = bytes([0x81, 0x80 | (length if length < 126 else 126)]) + \
            (struct.pack(">H", length) if length >= 126 else b"") + \
            mask + masked
    sock.sendall(frame)

    # Lê resposta
    raw = b""
    sock.settimeout(10)
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 2:
                break
    except Exception:
        pass
    sock.close()
    return True  # não precisamos do retorno para navegação


def get_tabs():
    try:
        return cdp_get("/json/list")
    except Exception:
        return []


def get_active_url():
    tabs = get_tabs()
    for tab in tabs:
        if tab.get("type") == "page":
            return tab.get("url", ""), tab.get("webSocketDebuggerUrl", "")
    return "", ""


def navigate(ws_url, url):
    try:
        cdp_ws_send(ws_url, "Page.navigate", {"url": url})
        time.sleep(3)
        return True
    except Exception as exc:
        log.error("Erro ao navegar via CDP: %s", exc)
        return False


def js_eval(ws_url, expression):
    try:
        cdp_ws_send(ws_url, "Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": False
        })
        return True
    except Exception as exc:
        log.error("Erro ao executar JS: %s", exc)
        return False


# ── Inicia / reconecta o Edge ────────────────────────────────────────────────
def start_edge():
    global _edge_proc
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
        "--kiosk",
        TARGET_URL,
    ])
    # Aguarda Edge abrir e expor o CDP
    for _ in range(20):
        time.sleep(1)
        try:
            cdp_get("/json/version")
            log.info("Edge iniciado e CDP disponível.")
            return True
        except Exception:
            pass
    log.error("Edge não respondeu ao CDP após 20 segundos.")
    return False


def is_edge_running():
    if _edge_proc and _edge_proc.poll() is None:
        try:
            cdp_get("/json/version")
            return True
        except Exception:
            pass
    return False


# ── Login ────────────────────────────────────────────────────────────────────
def do_login(ws_url_before_nav):
    log.info("Navegando para login: %s", LOGIN_URL)
    # Pega aba ativa
    _, ws_url = get_active_url()
    if not ws_url:
        log.error("Nenhuma aba disponível para login.")
        return False

    navigate(ws_url, LOGIN_URL)
    time.sleep(2)
    _, ws_url = get_active_url()
    if not ws_url:
        return False

    def q(s):
        return s.replace("'", "\\'")

    ok = all([
        js_eval(ws_url, f"document.querySelector('{q(USERNAME_SELECTOR)}').value = '{q(USERNAME)}'"),
        js_eval(ws_url, f"document.querySelector('{q(PASSWORD_SELECTOR)}').value = '{q(PASSWORD)}'"),
        js_eval(ws_url, f"document.querySelector('{q(SUBMIT_SELECTOR)}').click()"),
    ])
    if not ok:
        log.error("Falha ao preencher formulário de login.")
        return False

    time.sleep(3)
    url_after, _ = get_active_url()
    log.info("Após login, URL: %s", url_after)
    return True


# ── Verificação e recuperação ─────────────────────────────────────────────────
def current_url_matches():
    url, _ = get_active_url()
    match = url.startswith(TARGET_URL)
    if not match and url:
        log.warning("URL incorreta: '%s'", url)
    return match, url


def recover():
    log.info("=== Iniciando recuperação ===")
    _, ws_url = get_active_url()
    if ws_url:
        navigate(ws_url, TARGET_URL)
        time.sleep(3)
        match, url = current_url_matches()
        if match:
            log.info("Recuperado sem login.")
            return True

    # Precisa login
    do_login(ws_url)
    time.sleep(2)
    _, ws_url = get_active_url()
    if ws_url:
        navigate(ws_url, TARGET_URL)
        time.sleep(3)

    match, _ = current_url_matches()
    log.info("=== Recuperação %s ===", "OK" if match else "falhou")
    return match


# ── Loop principal ────────────────────────────────────────────────────────────
def run_monitor():
    log.info("Iniciando monitor | TARGET=%s | intervalo=%ds", TARGET_URL, CHECK_INTERVAL)

    if not is_edge_running():
        if not start_edge():
            sys.exit(1)
    else:
        log.info("Edge já está em execução.")

    time.sleep(2)
    match, url = current_url_matches()
    if not match:
        recover()
    else:
        log.info("Telão já está na página correta: %s", url)

    while _running:
        time.sleep(CHECK_INTERVAL)
        if not _running:
            break
        try:
            if not is_edge_running():
                log.warning("Edge fechou. Reiniciando...")
                start_edge()
                time.sleep(2)

            match, _ = current_url_matches()
            if not match:
                recover()
            else:
                log.debug("OK")
        except Exception as exc:
            log.error("Erro inesperado: %s", exc)

    log.info("Monitor encerrado.")


if __name__ == "__main__":
    run_monitor()
