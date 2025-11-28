# logger.py
from datetime import datetime
# logger.py
from pprint import pformat
import time



start_time = datetime.now()
logs = []  # lista globale dei log

# def log(message: str):
#     """Aggiunge un messaggio con timestamp relativo."""
#     elapsed = (datetime.now() - start_time).total_seconds()
#     timestamp = f"[+{elapsed:.1f}s]"
#     logs.append(f"{timestamp} {message}")
#     print(f"{timestamp} {message}")

# def log(message: str):
#     elapsed = (datetime.now() - start_time).total_seconds()
#     timestamp = f"[+{elapsed:.1f}s]"

#     for line in message.splitlines():
#         line_to_print = f"{timestamp} {line}"
#         logs.append(line_to_print)
#         print(line_to_print)

def log(message):
    if isinstance(message, dict):
        message = pformat(message)
    elif not isinstance(message, str):
        message = str(message)

    for line in message.splitlines():
        print(f"[+{time.time():.1f}s] {line}")

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
