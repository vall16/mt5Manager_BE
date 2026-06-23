# logger.py
from datetime import datetime
from decimal import Decimal
import json
from pprint import pformat
import time
import sys
import io
import re



start_time = datetime.now()
logs = []  # lista globale dei log

# ── ANSI colori ──
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

def colorize_signal(text: str) -> str:
    # 🔴 Intera riga in rosso se status è "ko"
    if re.search(r'"status"\s*:\s*"ko"', text, re.IGNORECASE):
        return f"{RED}{text}{RESET}"
    # 💛 BUY/SELL signal per <SIMBOLO> → giallo, simbolo rosso
    return re.sub(
        r'(BUY|SELL)\s+signal\s+per\s+(\S+)',
        lambda m: f"{YELLOW}{m.group(1)} signal per {RESET}{RED}{m.group(2)}{RESET}",
        text,
        flags=re.IGNORECASE,
    )


def log(message):
    global logs

    def default_serializer(obj):
        if isinstance(obj, Decimal):
            return float(obj)   # oppure str(obj) se preferisci
        return str(obj)

    # converte in stringa leggibile
    if isinstance(message, dict):
        message = json.dumps(
            message,
            indent=2,
            ensure_ascii=False,
            default=default_serializer   # <- qui la magia
        )
    elif not isinstance(message, str):
        message = str(message)

    # elapsed = (datetime.now() - start_time).total_seconds()
    # timestamp = f"[+{elapsed:.1f}s]"

    # 📅 timestamp stile italiano SENZA secondi
    timestamp = datetime.now().strftime("[%d/%m/%Y %H:%M]")


    for line in message.splitlines():
        line_to_print = f"{timestamp} {line}"
        # 1. Salva in memoriaa
        logs.append(line_to_print)
        # 2. Stampa a video con colori ANSI
        colored = colorize_signal(line_to_print)
        print(colored, flush=True)
        # 3. SCRIVI SU FILE (SEMPRE SENZA COLORI)
        with open("server_log.txt", "a", encoding="utf-8") as f:
            f.write(line_to_print + "\n")


import requests

class FakeResponse:
    def __init__(self, status_code=500, json_data=None, error_msg="Errore di rete"):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.error_msg = error_msg
        self.text = error_msg

    def json(self):
        return self._json_data

    def raise_for_status(self):
        raise requests.HTTPError(
            f"FakeResponse HTTP {self.status_code}: {self.error_msg}"
        )

def safe_get(url, timeout=3):
    try:
        return requests.get(url, timeout=timeout)
    except requests.exceptions.ConnectionError:
        log(f"❌ Connessione fallita verso {url}")
        return FakeResponse(status_code=503, error_msg=f"Connessione fallita verso {url}")
    except requests.exceptions.Timeout:
        log(f"⏳ Timeout chiamando {url}")
        return FakeResponse(status_code=504, error_msg=f"Timeout chiamando {url}")
    except Exception as e:
        log(f"⚠️ Errore GET imprevisto: {e}")
        return FakeResponse(status_code=500, error_msg=f"Errore GET imprevisto: {e}")
    

def safe_post(url, json=None, timeout=3):
    try:
        return requests.post(url, json=json, timeout=timeout)
    except requests.exceptions.ConnectionError:
        log(f"❌ Connessione fallita verso {url}")
        return FakeResponse(status_code=503, error_msg=f"Connessione fallita verso {url}")
    except requests.exceptions.Timeout:
        log(f"⏳ Timeout chiamando {url}")
        return FakeResponse(status_code=504, error_msg=f"Timeout chiamando {url}")
    except Exception as e:
        log(f"⚠️ Errore POST imprevisto: {e}")
        return FakeResponse(status_code=500, error_msg=f"Errore POST imprevisto: {e}")
