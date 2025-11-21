# logger.py
from datetime import datetime

start_time = datetime.now()
logs = []  # lista globale dei log

def log(message: str):
    """Aggiunge un messaggio con timestamp relativo."""
    elapsed = (datetime.now() - start_time).total_seconds()
    timestamp = f"[+{elapsed:.1f}s]"
    logs.append(f"{timestamp} {message}")
    print(f"{timestamp} {message}")
