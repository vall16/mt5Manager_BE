import subprocess
import re

PORTS = [8000, 8001, 8002, 8003]


def find_pid(port):
    """
    Trova il PID che sta ascoltando sulla porta.
    Usa netstat (Windows).
    """
    try:
        result = subprocess.check_output(
            f"netstat -ano | findstr :{port}",
            shell=True, text=True
        ).strip().splitlines()

        for line in result:
            parts = re.split(r"\s+", line)
            if len(parts) >= 5:
                pid = parts[-1]
                return pid
    except:
        return None

    return None


def kill_pid(pid):
    """
    Termina un processo con taskkill.
    """
    try:
        print(f"🛑 Kill PID {pid}")
        subprocess.call(f"taskkill /F /PID {pid}", shell=True)
    except Exception as e:
        print(f"Errore uccidendo PID {pid} → {e}")


def main():
    print("🔍 Sto cercando server Uvicorn su:", PORTS)

    for port in PORTS:
        pid = find_pid(port)

        if pid:
            print(f"➡️ Porta {port} è occupata da PID {pid} → TERMINO...")
            kill_pid(pid)
        else:
            print(f"✔️ Porta {port} è libera (nessun server)")

    print("\n✅ Tutte le istanze sono state terminate.")


if __name__ == "__main__":
    main()
