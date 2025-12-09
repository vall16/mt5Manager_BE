# logger.py
from datetime import datetime
# logger.py
from decimal import Decimal
import json
from pprint import pformat
import time
import sys
import io

# Forza stdout e stderr su UTF-8 (Windows)
# sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
# sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


start_time = datetime.now()
logs = []  # lista globale dei log


# def log(message):
#     global logs
#     # converte in stringa leggibile
#     if isinstance(message, dict):
#         # message = pformat(message)
#         message = json.dumps(message, indent=2, ensure_ascii=False)

#     elif not isinstance(message, str):
#         message = str(message)

#     # timestamp relativo
#     elapsed = (datetime.now() - start_time).total_seconds()
#     timestamp = f"[+{elapsed:.1f}s]"

#     for line in message.splitlines():
#         line_to_print = f"{timestamp} {line}"
#         logs.append(line_to_print)   # <-- mantiene lo storico
#         print(line_to_print, flush=True)  


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

    elapsed = (datetime.now() - start_time).total_seconds()
    timestamp = f"[+{elapsed:.1f}s]"

    for line in message.splitlines():
        line_to_print = f"{timestamp} {line}"
        logs.append(line_to_print)
        print(line_to_print, flush=True)
# start_time = time.time()

# def log(message):
#     global start_time
#     if isinstance(message, (dict, list)):
#         message = pformat(message)
#     elif not isinstance(message, str):
#         message = str(message)

#     # interpreta \n
#     # message = message.encode('utf-8').decode('unicode_escape')

#     elapsed = time.time() - start_time  # float - float
#     timestamp = f"[+{elapsed:.1f}s]"

#     for line in message.splitlines():
#         line_to_print = f"{timestamp} {line}"
#         logs.append(line_to_print)
#         print(line_to_print)


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


# def safe_get(url, timeout=10):
#     try:
#         resp = requests.get(url, timeout=timeout)
#         return resp
#     except Exception as e:
#         # Crea una risposta finta ma *compatibile*
#         return FakeResponse(
#             status_code=500,
#             json_data={"error": str(e)},
#             error_msg=str(e)
#         )

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
    # return requests.get(url, timeout=timeout)

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
