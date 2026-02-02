# logger.py
from datetime import datetime
from decimal import Decimal
import json
from pprint import pformat
import time
import sys
import io



start_time = datetime.now()
logs = []  # lista globale dei log



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
        # 2. Stampa a video
        print(line_to_print, flush=True)
        # 3. SCRIVI SU FILE (Aggiunta)
        with open("server_log.txt", "a", encoding="utf-8") as f:
            f.write(line_to_print + "\n")


import requests

class FakeResponse:
    def __init__(self, status_code=500, json_data=None, error_msg="Errore di rete"):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.error_msg = error_msg

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
        return None
    except requests.exceptions.Timeout:
        log(f"⏳ Timeout chiamando {url}")
        return None
    except Exception as e:
        log(f"⚠️ Errore GET imprevisto: {e}")
        return None
    

def safe_post(url, json=None, timeout=3):
    try:
        return requests.post(url, json=json, timeout=timeout)
    except requests.exceptions.ConnectionError:
        log(f"❌ Connessione fallita verso {url}")
        return None
    except requests.exceptions.Timeout:
        log(f"⏳ Timeout chiamando {url}")
        return None
    except Exception as e:
        log(f"⚠️ Errore POST imprevisto: {e}")
        return None
