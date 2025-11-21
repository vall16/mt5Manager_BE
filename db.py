from datetime import datetime
import json
from logging import info

from pydantic import BaseModel
from logger import log, logs
from typing import List
import uuid
import mysql.connector
from mysql.connector import Error as MySQLError
import requests
from models import LoginRequest, LoginResponse, ServerRequest, TraderServersUpdate,Trader, Newtrader,UserResponse, ServerResponse
from fastapi import FastAPI, HTTPException
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware
import MetaTrader5 as mt5
import bcrypt
import os
import re
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv()


# pool = mysql.connector.pooling.MySQLConnectionPool(
#     pool_name=os.environ.get("MYSQL_POOL_NAME"),
#     pool_size=int(os.environ.get("MYSQL_POOL_SIZE")),
#     host=os.environ.get("MYSQL_HOST"),
#     user=os.environ.get("MYSQL_USER"),
#     password=os.environ.get("MYSQL_PASSWORD"),
#     database=os.environ.get("MYSQL_DB"),
#     port=int(os.environ.get("MYSQL_PORT")),
# )

# from fastapi_utils.tasks import repeat_every

router = APIRouter()

# logs = []  # elenco dei messaggi di log

start_time = datetime.now()  
# funz messaggistica di log
# def log(message: str):
#         """Aggiunge un messaggio con timestamp relativo."""
#         elapsed = (datetime.now() - start_time).total_seconds()
#         timestamp = f"[+{elapsed:.1f}s]"
#         logs.append(f"{timestamp} {message}")
#         print(f"{timestamp} {message}")  # Mantieni anche la stampa in console

import requests

def ensure_mt5_initialized(base_url: str, mt5_path: str, log=print):
    """
    Verifica la salute del terminale remoto MT5 e lo inizializza se necessario.
    Riutilizzabile in qualsiasi logica master/slave.
    """
    health_url = f"{base_url}/health"
    init_url   = f"{base_url}/init-mt5"

    log(f"🔍 Controllo MT5 remoto su {health_url}...")

    try:
        resp = requests.get(health_url, timeout=5)
    except Exception as e:
        raise Exception(f"❌ Terminale non raggiungibile ({base_url}): {e}")

    # Se MT5 è già attivo
    if resp.status_code == 200:
        data = resp.json()
        if data.get("status") == "ok":
            log(f"✅ MT5 attivo (versione {data.get('mt5_version')})")
            return True

    # Se non è attivo → inizializza
    log(f"🔹 Inizializzo terminale MT5 su {init_url}...")
    init_body = {"path": mt5_path}

    try:
        init_resp = requests.post(init_url, json=init_body, timeout=10)
    except Exception as e:
        raise Exception(f"❌ Errore durante init su {base_url}: {e}")

    if init_resp.status_code != 200:
        raise Exception(f"❌ Init MT5 fallita ({base_url}): {init_resp.text}")

    log(f"✅ MT5 inizializzato correttamente su {base_url}")
    return True

# funzione di login a mster5
def mt5_login(base_url, login, password, server, log):
    login_url = f"{base_url}/login"
    login_body = {
        "login": int(login),
        "password": password,
        "server": server
    }

    log(f"🔹 Connessione MT5 a {login_url}")
    resp = requests.post(login_url, json=login_body, timeout=10)

    if resp.status_code != 200:
        raise Exception(f"❌ Login fallito su {base_url}: {resp.text}")

    data = resp.json()
    log(f"✅ Login MT5 OK! Bilancio: {data.get('balance')}")

    return data


def get_connection():
    try:

        # conn = mysql.connector.connect(
        #     host=os.environ.get("MYSQL_HOST"),
        #     user=os.environ.get("MYSQL_USER"),
        #     password=os.environ.get("MYSQL_PASSWORD"),
        #     database=os.environ.get("MYSQL_DB"),
        #     port=int(os.environ.get("MYSQL_PORT")),
        #     connection_timeout=int(os.environ.get("MYSQL_CONNECT_TIMEOUT", 5)),
        #     read_timeout=int(os.environ.get("MYSQL_READ_TIMEOUT", 60)),
        #     write_timeout=int(os.environ.get("MYSQL_WRITE_TIMEOUT", 60)),
        # )

        # print(os.environ.get("MYSQL_HOST"))
        # print(os.environ.get("MYSQL_DB"))
        # print(os.environ.get("MYSQL_PORT"))
        


        # db locale 
        conn = mysql.connector.connect(
            host="127.0.0.1",       # o "127.0.0.1"
            user="trader",            # utente MySQL locale
            password="vibe2025",            # lascia vuoto se non hai password
            database="trader_db",   # nome del tuo database
            port=3306               # porta predefinita MySQL
        )


        return conn
    except MySQLError as e:
        raise HTTPException(status_code=500, detail=f"Errore di connessione MySQL: {e}")

# funz che recupera il trader corrente
def get_trader(cursor, trader_id):
    cursor.execute("""
        SELECT t.id, t.name, t.moltiplicatore, t.fix_lot, t.sl, t.tp, t.tsl,
               ms.server AS master_name, ms.user AS master_user, ms.pwd AS master_pwd, ms.path AS master_path, ms.ip AS master_ip, ms.port AS master_port,
               ss.server AS slave_name, ss.user AS slave_user, ss.pwd AS slave_pwd, ss.path AS slave_path, ss.ip AS slave_ip, ss.port AS slave_port
        FROM traders t
        JOIN servers ms ON ms.id = t.master_server_id
        JOIN servers ss ON ss.id = t.slave_server_id
        WHERE t.id = %s
    """, (trader_id,))
    trader = cursor.fetchone()
    # log(logs, start_time, f"Trader info: {trader}")
    log("=== Trader Info ===")
    log(trader)
    log("===================")
    log(trader["master_name"])
    log(trader["master_user"])
    log(trader["master_pwd"])
    log(trader["master_ip"])
    log(trader["master_port"])

    return trader


# funzione di tentativo mappatura/cleaning del simbolo da master a slave
def normalize_symbol(symbol: str) -> str:
    """
    Rimuove suffissi o prefissi comuni dai simboli per permettere la ricerca cross-broker.
    Esempi:
      - XAUUSD-STD → XAUUSD
      - EURUSD.m → EURUSD
      - EURUSDpro → EURUSD
      - US30.cash → US30
    """
    # Rimuove punti, trattini e suffissi come -STD, .m, .pro, ecc.
    cleaned = re.sub(r'[-_.](std|stp|pro|ecn|m|mini|micro|cash|r)$', '', symbol, flags=re.IGNORECASE)

    # Rimuove eventuali spazi o caratteri extra
    cleaned = cleaned.strip().upper()

    return cleaned




@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, username, password FROM users WHERE username=%s",
        (req.username,)
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return LoginResponse(success=False, message="Invalid credentials", user=None)

    # Controllo password con bcrypt
    if bcrypt.checkpw(req.password.encode(), user["password"].encode()):
        # Aggiorna last_login
        cursor.execute(
            "UPDATE users SET last_login = NOW() WHERE id=%s",
            (user["id"],)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return LoginResponse(
            success=True,
            user=UserResponse(id=user["id"], username=user["username"]),
            message="Login successful"
        )

    cursor.close()
    conn.close()
    return LoginResponse(success=False, message="Invalid credentials", user=None)

def create_user(username: str, password: str):
    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()  # genera hash dinamico
    user_id = str(uuid.uuid4())  # genera un id unico

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (id, username, password) VALUES (%s, %s, %s)",
        (user_id, username, hashed_pw)
    )
    conn.commit()
    cursor.close()
    conn.close()
    print(f"User {username} creato con successo. ID: {user_id}")


@router.get("/servers", response_model=List[ServerResponse])
def get_servers():
    try:
        conn = get_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")

        cursor = conn.cursor(dictionary=True)
        # cursor.execute("SELECT * FROM servers")
        cursor.execute("SELECT * FROM servers")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        return rows

    except mysql.connector.Error as db_err:
        # Errore specifico MySQL
        detail = f"MySQL error {db_err.errno}: {db_err.msg}"
        print(f"❌ {detail}")
        raise HTTPException(status_code=500, detail=detail)

    except Exception as e:
        # Qualsiasi altro errore
        print(f"❌ Errore imprevisto in get_servers: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.post("/servers")
def insert_server(server: ServerRequest):

    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        query = """
            # INSERT INTO servers 
            INSERT INTO servers 
            (`user`, `pwd`, `server`,`server_alias`, `platform`, `ip`, `path`, `port`, `is_active`, `created_at`, `updated_at`)
            VALUES (%s, %s, %s, %s,%s, %s, %s, %s, %s, NOW(), NOW())
        """

        values = (
            server.user,
            server.pwd,
            server.server,
            server.server_alias,
            server.platform,
            server.ip,
            server.path,
            server.port,
            server.is_active
        )

        print("\n🧩 [DEBUG SQL] Tentativo di INSERT su 'servers' ...")
        print("Query SQL:", query)
        print("Valori:", values)

        cursor.execute(query, values)
        conn.commit()

        new_id = cursor.lastrowid
        print(f"✅ [OK] Inserito record servers.id={new_id}")

        cursor.close()
        conn.close()

        return {"message": "Server added successfully", "id": new_id}

    except MySQLError as e:
        import traceback
        print("\n❌ [ERRORE SQL MySQL]")
        print("Codice errore:", e.errno)
        print("Dettaglio:", e.msg)
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
        raise HTTPException(status_code=500, detail=f"MySQL Error {e.errno}: {e.msg}")

    except Exception as e:
        import traceback
        print("\n❌ [ERRORE GENERICO]")
        print("Tipo errore:", type(e).__name__)
        print("Dettaglio:", str(e))
        traceback.print_exc()
        try:
            conn.rollback()
        except:
            pass
        raise HTTPException(status_code=500, detail=f"Errore: {str(e)}")


@router.delete("/servers/{server_id}")
def delete_server(server_id: int):
    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()
        # Verifica se il server esiste
        cursor.execute("SELECT id FROM servers WHERE id = %s", (server_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Server not found")

        # Cancella il server
        cursor.execute("DELETE FROM servers WHERE id = %s", (server_id,))
        conn.commit()

        cursor.close()
        conn.close()

        return {"message": f"Server {server_id} deleted successfully"}

    except MySQLError as e:
        print("=== ERRORE DURANTE DELETE SERVER ===")
        print(e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

# Endpoint per recuperare tutti i trader
@router.get("/traders", response_model=List[Trader])
def get_traders():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM traders")
    rows = cursor.fetchall()
    conn.close()

    # Mappa is_active -> status
    traders = []
    for row in rows:
        traders.append({
            "id": row["id"],
            "name": row["name"],
            "status": "active" if row["is_active"] else "inactive",
            "master_server_id": row.get("master_server_id"),
            "slave_server_id": row.get("slave_server_id"),
            "sl": row.get("sl"),
            "tp": row.get("tp"),
            "tsl": row.get("tsl"),
            "moltiplicatore": row.get("moltiplicatore"),
            "fix_lot": row.get("fix_lot"),
            "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
            "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        })
    return traders

# --- INSERT trader ---
@router.post("/traders")
def insert_trader(trader: Newtrader):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        query = """
        INSERT INTO traders
        (name, master_server_id, slave_server_id, 
         sl, tp, tsl, moltiplicatore, fix_lot, is_active, created_at, updated_at)
        VALUES (%s,  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        now = datetime.now()

        values = (
            trader.name,
            trader.master_server_id,
            trader.slave_server_id,
            trader.sl,
            trader.tp,
            trader.tsl,
            trader.moltiplicatore or 1.0,
            trader.fix_lot,
            trader.status == 'active',  # converte 'active'/'inactive' in booleano
            now,
            now
        )



        cursor.execute(query, values)
        conn.commit()

        new_id = cursor.lastrowid
        cursor.close()
        conn.close()

        return {"status": "success", "id": new_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




    # --- DELETE trader ---

@router.delete("/traders/{trader_id}")
def delete_trader(trader_id: int):
    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()
        # Verifica se il trader esiste
        cursor.execute("SELECT id FROM traders WHERE id = %s", (trader_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Trader not found")

        # Cancella il trader
        cursor.execute("DELETE FROM traders WHERE id = %s", (trader_id,))
        conn.commit()

        cursor.close()
        conn.close()

        return {"status": "success", "message": f"Trader {trader_id} deleted successfully"}

    except Exception as e:
        print("=== ERRORE DURANTE DELETE TRADER ===")
        print(e)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


@router.put("/traders/{trader_id}/servers")
def update_trader_servers(trader_id: int, update: TraderServersUpdate):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # 🔹 Controlla se il trader esiste
    cursor.execute("SELECT * FROM traders WHERE id = %s", (trader_id,))
    trader = cursor.fetchone()
    if not trader:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Trader not found")

    # 🔹 Prepara i valori aggiornabili dinamicamente
    fields = []
    values = []

    if update.master_server_id is not None:
        fields.append("master_server_id = %s")
        values.append(update.master_server_id)
    if update.slave_server_id is not None:
        fields.append("slave_server_id = %s")
        values.append(update.slave_server_id)
    if update.sl is not None:
        fields.append("sl = %s")
        values.append(update.sl)
    if update.tp is not None:
        fields.append("tp = %s")
        values.append(update.tp)
    if update.tsl is not None:
        fields.append("tsl = %s")
        values.append(update.tsl)
    if update.moltiplicatore is not None:
        fields.append("moltiplicatore = %s")
        values.append(update.moltiplicatore)

    # 🔹 Se non c’è nulla da aggiornare, ritorna il trader com’è
    if not fields:
        cursor.close()
        conn.close()
        return trader

    # 🔹 Costruisci dinamicamente la query
    query = f"UPDATE traders SET {', '.join(fields)} WHERE id = %s"
    
    values.append(trader_id)

    # 🔹 Log dettagliato per debug
    # 🔹 Log completo e leggibile
    print("🛠️ [UPDATE TRADER] Esecuzione aggiornamento trader")
    print("──────────────────────────────────────────────")
    print(f"🔹 Trader ID: {trader_id}")
    print("🔹 Campi aggiornati:")
    for f, v in zip(fields, values[:-1]):  # salta l'ID alla fine
        print(f"   • {f.replace(' = %s', '')} → {v}")
    print("──────────────────────────────────────────────")
    print(f"🧾 SQL: {query}")
    print(f"💾 VALUES: {values}")
    print("──────────────────────────────────────────────")


    cursor.execute(query, tuple(values))
    conn.commit()

    # 🔹 Recupera il trader aggiornato
    cursor.execute("SELECT * FROM traders WHERE id = %s", (trader_id,))
    updated_trader = cursor.fetchone()

    cursor.close()
    conn.close()

    return {
        "status": "ok",
        "message": "Trader aggiornato con successo",
        "trader": updated_trader
    }


# funziona che copia gli ordini del master sullo slave e aggiorna le tabelle relative nel
@router.post("/traders/{trader_id}/copy_orders")
def copy_orders(trader_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    logs = []  # elenco dei messaggi di log

    # start_time = datetime.now()  

    # def log(message: str):
    #     """Aggiunge un messaggio con timestamp relativo."""
    #     elapsed = (datetime.now() - start_time).total_seconds()
    #     timestamp = f"[+{elapsed:.1f}s]"
    #     logs.append(f"{timestamp} {message}")
    #     print(f"{timestamp} {message}")  # Mantieni anche la stampa in console


    log("🚀 Entrato in copy_orders()")

    cursor = conn.cursor(dictionary=True)

    # # 1️⃣ Recupera info del trader (master e slave)
    trader = get_trader(cursor, trader_id)

    if not trader:
        
        return {"status": "ko", "message": "Trader non trovato", "logs": logs}

    """
    Inizializza e connette il master MT5 remoto tramite API HTTP.
    """

    # # 1️⃣ Inizializza terminale remoto master

    base_url_master = f"http://{trader['master_ip']}:{trader['master_port']}"
    ensure_mt5_initialized(base_url_master, trader["master_path"], log)

        
    # 2️⃣ Login remoto a master
    master_data = mt5_login(
        base_url_master,
        trader["master_user"],
        trader["master_pwd"],
        trader["master_name"],
        log
    )
    log(f"✅ Connessione al master {trader['master_user']} riuscita! Bilancio: {master_data.get('balance')}")


    # recupera posizioni  del server master
    base_url_master = f"http://{trader['master_ip']}:{trader['master_port']}"

    try:
        positions_url = f"{base_url_master}/positions"
        log(f"🔹 Recupero posizioni dal master via {positions_url}")

        resp = requests.get(positions_url, timeout=10)
        if resp.status_code != 200:
            log(f"❌ Errore API master: {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Errore dal master API: {resp.text}")

        master_positions = resp.json()
        # master_positions = data.get("positions", [])

        if not master_positions:
            log("⚠️ Nessuna posizione aperta sul master.")

        log(f"✅ Posizioni master ricevute: {len(master_positions)}")
        
    except requests.exceptions.RequestException as e:
        log(f"❌ Errore di connessione al master API: {e}")
        raise HTTPException(status_code=500, detail=f"Errore connessione al master: {str(e)}")


    # Stampa tutte le posizioni trovate
    log("=== POSIZIONI SUL MASTER ===")
    for pos in master_positions:
        log(f"[MASTER] {pos}")  # pos è già un dict


    # # 3️⃣ Connessione allo slave MT5
    """
    Inizializza e connette il slave MT5 remoto tramite API HTTP.
    """

    base_url_slave = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    ensure_mt5_initialized(base_url_slave, trader["slave_path"], log)


    # 2️⃣ Login remoto a slave
    login_url = f"{base_url_slave}/login"
    login_body = {
        "login": int(trader["slave_user"]),
        "password": trader["slave_pwd"],
        "server": trader["slave_name"]
    }
    log("=" * 80)
    log("🔹 Tentativo di connessione allo SLAVE")
    log(f"🌐 URL login: {login_url}")
    log(f"👤 Login data:")
    log(f"   - Login ID: {trader['slave_user']}")
    log(f"   - Password: {trader['slave_pwd']}")
    log(f"   - Server:   {trader['slave_name']}")
    log("=" * 80)


    log(f"🔹 Connessione allo slave via {login_url}")
    try:
        resp = requests.post(login_url, json=login_body, timeout=30)
        log(f"📡 Status code: {resp.status_code}")
        log(f"📩 Response: {resp.text}")
    except Exception as e:
        log(f"❌ Errore chiamata login slave: {e}")
        raise

    if resp.status_code != 200:
        raise Exception(f"❌ Login fallito su {base_url_slave}: {resp.text}")

    data = resp.json()
    log(f"✅ Connessione allo slave {trader['slave_user']} riuscita! Bilancio: {data.get('balance')}")


    # 4️⃣ Copia ogni ordine master sullo slave (SE NON C'è GIA')
    import traceback

    base_url = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    slave_positions  = get_slave_positions(base_url);
    
    for pos in master_positions:
        try:
            symbol = pos["symbol"]
            order_type = "buy" if pos["type"] == 0 else "sell"
            volume = trader["fix_lot"] or round(pos["volume"] * float(trader["moltiplicatore"]), 2)
            log(f"Master symbol: {symbol}, tipo: {order_type}, volume calcolato per slave: {volume}")

            # 🔹 NUOVO: Controllo se l’ordine esiste già sullo slave
            slave_exists = any(
                sp["symbol"] == symbol and
                (sp["type"] == order_type or sp["type"] == pos["type"]) and
                abs(sp["volume"] - volume) < 0.01
                for sp in slave_positions
            )
            if slave_exists:
                log(f"⚠️ Ordine {symbol} {order_type} già presente sullo slave, salto invio")
                continue

            # 🔹 1️⃣ Controllo se il simbolo è disponibile e visibile sullo slave
            info_url = f"{base_url}/symbol_info/{symbol}"
            log(f"🔍 Richiedo info simbolo allo slave: {info_url}")
            
            resp = requests.get(info_url, timeout=10)

            sym_info = resp.json()

            if resp.status_code != 200:
                log(f"⚠️ Impossibile ottenere info per {symbol} dallo slave: {resp.text}")
                continue

                
            if not sym_info.get("visible", False):
                log(f"🔹 Simbolo {symbol} non visibile. Provo ad abilitarlo...")
                if not mt5.symbol_select(symbol, True):
                    log(f"❌ Errore: impossibile attivare {symbol} sullo slave.")
                    # 🔹 Tentativo con simbolo normalizzato
                    normalized_symbol = normalize_symbol(symbol)
                    
                    if normalized_symbol != symbol:
                        log(f"🔄 Tentativo 2: provo simbolo normalizzato '{normalized_symbol}'...")
                        info_url = f"{base_url}/symbol_info/{normalized_symbol}"
                        try:
                            resp = requests.get(info_url, timeout=10)
                            sym_info = resp.json()
                        except Exception as e:
                            log(f"⚠️ Errore richiesta info simbolo normalizzato {normalized_symbol}: {e}")
                            continue

                        if resp.status_code == 200 and sym_info.get("visible", False):
                            log(f"✅ Simbolo normalizzato '{normalized_symbol}' trovato sullo slave.")
                            # ** il simbolo dello slave è quello normalizzato
                            symbol = normalized_symbol
                        else:
                            log(f"❌ Anche simbolo normalizzato '{normalized_symbol}' non trovato sullo slave, salto ordine.")
                            continue

                    else:
                        log(f"✅ Simbolo {symbol} attivato con successo sullo slave.")
            else:
                log(f"✅ Simbolo {symbol} è già visibile sullo slave.")

            
            # 🔹 2️⃣ Recupero tick dal server slave via API
            tick_url = f"{base_url}/symbol_tick/{symbol}"
            log(f"📡 Richiedo tick allo slave: {tick_url}")

            
            resp_tick = requests.get(tick_url, timeout=10)
            if resp_tick.status_code != 200:
                log(f"⚠️ Nessun tick disponibile per {symbol} dallo slave: {resp_tick.text}")
                continue

            tick = resp_tick.json()
            if not tick or "bid" not in tick or "ask" not in tick:
                log(f"⚠️ Tick incompleto o non valido per {symbol}: {tick}")
                continue

            log(f"✅ Tick ricevuto per {symbol}: bid={tick['bid']}, ask={tick['ask']}")

            # --- CALCOLO SL IN PIP ---
            sl_pips = trader["sl"]  # valore pip inserito dal trader nell'app (es. 10)

            if sl_pips and float(sl_pips) > 0:
                pip_value = float(sym_info.get("point"))  # valore del singolo punto del simbolo
                sl_distance = float(sl_pips) * pip_value

                if order_type == "buy":
                    # SL sotto il prezzo ask
                    calculated_sl = tick["ask"] - sl_distance
                else:
                    # SL sopra il prezzo bid (per SELL)
                    calculated_sl = tick["bid"] + sl_distance
            else:
                calculated_sl = None  # se sl=0 non imposta SL

            # calcolo TP da input
            tp_pips = trader["tp"]  # pips inseriti dall'app
            pip_value = float(sym_info.get("point"))

            if tp_pips and float(tp_pips) > 0:
                tp_distance = float(tp_pips) * pip_value
                if order_type == "buy":
                    calculated_tp = tick["ask"] + tp_distance
                else:
                    calculated_tp = tick["bid"] - tp_distance
            else:
                calculated_tp = None  # se TP=0 non impostare TP


            # 🔹 3️⃣ Preparo la richiesta da inviare allo slave
            request = {
                "symbol": symbol,
                "volume": volume,
                "type": "buy" if order_type == "buy" else "sell",
                "price": tick["ask"] if order_type == "buy" else tick["bid"],
                "sl": calculated_sl,
                "tp": calculated_tp,
                "comment": f"Copied from master {trader_id}",
            }

            # 🔹 3️⃣ Invio ordine allo slave via API
            order_url = f"{base_url}/order"
            log(f"🔁 Invio ordine allo slave via API: {order_url}")
            log(f"🧾 Dati inviati: {json.dumps(request, indent=2)}")

            try:
                resp_order = requests.post(order_url, json=request, timeout=20)

                if resp_order.status_code != 200:
                    log(f"❌ Errore invio ordine allo slave: {resp_order.text}")
                    continue

                result = resp_order.json()
                log(f"✅ Risposta dallo slave: {result}")

            except requests.exceptions.RequestException as e:
                log(f"⚠️ Errore di connessione con lo slave: {e}")
                continue

            # if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            if result and result.get("result", {}).get("retcode") == mt5.TRADE_RETCODE_DONE:

                # 🔹 Inserimento nel DB master_orders
                
                # Controlla se l'ordine master esiste già
                cursor.execute("""
                    SELECT id
                    FROM master_orders
                    WHERE trader_id = %s AND ticket = %s
                """, (trader_id, pos.get("ticket")))

                existing = cursor.fetchone()

                if existing:
                    master_order_id = existing["id"]
                    log(f"⚠️ Ordine master {pos.get('ticket')} già presente, salto insert")
                else:
                    # Inserisce un nuovo ordine master
                    cursor.execute("""
                        INSERT INTO master_orders (trader_id, ticket, symbol, type, volume, price_open, sl, tp, opened_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        trader_id, pos.get("ticket"), symbol, order_type, pos.get("volume"),
                        pos.get("price_open"), pos.get("sl"), pos.get("tp"),
                        datetime.fromtimestamp(pos.get("time"))
                    ))
                    master_order_id = cursor.lastrowid
                    conn.commit()
                    log(f"✅ Ordine master {pos.get('ticket')} inserito")



                # 🔹 Inserimento nel DB slave_orders (aggiunto master_ticket)
                cursor.execute("""
                    INSERT INTO slave_orders (
                        trader_id, master_order_id, master_ticket, ticket,
                        symbol, type, volume, price_open, sl, tp, opened_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    trader_id,
                    master_order_id,
                    pos.get("ticket"),  # <-- master_ticket
                    result.get("result", {}).get("order"),  # ticket slave
                    symbol,
                    order_type,
                    volume,
                    request["price"],
                    pos.get("sl"),
                    pos.get("tp")
                ))

                conn.commit()
                log(f"✅ Ordine copiato e registrato: {symbol}")

                

        except Exception as e:
            log("❌ Eccezione durante la copia ordine:")
            log(traceback.format_exc())
        continue

    # ✅ ✅ ✅ A questo punto IL CICLO È FINITO → ora posso gestire LE CHIUSURE
    log("🔍 Avvio sincronizzazione chiusure master → slave ...")
    master_base = f"http://{trader['master_ip']}:{trader['master_port']}"
    slave_base  = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    log(f"🌐 master_base URL → {master_base}")
    log(f"🌐 slave_base  URL → {slave_base}")


    try:
        # 1️⃣ Recupero posizioni master
        master_positions = get_master_positions(master_base)
        master_tickets = [mp["ticket"] for mp in master_positions]
        log(f"✅ Ticket master attivi: {master_tickets}")

        # 2️⃣ Recupero ticket slave dal DB
        cursor.execute("""
            SELECT ticket AS slave_ticket, master_ticket
            FROM slave_orders
            WHERE trader_id = %s AND closed_at IS NULL
        """, (trader_id,))
        slave_orders = cursor.fetchall()
        log(f"✅ Ticket slave attivi nel DB: {[s['slave_ticket'] for s in slave_orders]}")

        # 3️⃣ Chiudo gli ordini slave il cui master non esiste più
        for so in slave_orders:
            slave_ticket = so["slave_ticket"]
            master_ticket = so["master_ticket"]

            if master_ticket not in master_tickets:
                log(f"⚠️ Ticket slave {slave_ticket} (master {master_ticket}) non più attivo → chiudo")

                # Chiudi tramite API dello slave
                resp = close_slave_order(slave_base, slave_ticket)


            # Recupera il profit dal risultato della chiusura (assumendo resp ritorni 'profit')
                profit = resp.get("profit", 0)

                # Aggiorna DB con chiusura e profit
                cursor.execute(
                    "UPDATE slave_orders SET closed_at=NOW(), profit=%s WHERE ticket=%s",
                    (profit, slave_ticket)
                )
                conn.commit()
                log(f"✅ Ticket slave {slave_ticket} chiuso, profit aggiornato: {profit}")



    except Exception as e:
        log(f"❌ Errore sincronizzazione chiusure: {str(e)}")
        

    # ✅ Pulizia finale
    mt5.shutdown()
    cursor.close()
    conn.close()


    # Restituiamo solo i dati del trader come test
    # return {"message": "Trader info retrieved", "trader": trader}
    return {
        "status": "ok",
        "message": "Operazione completata",
        "logs": logs
    }

def get_master_positions(master_base_url):
    url = f"{master_base_url}/positions"
    log(f"🔹 Recupero posizioni master: {url}")
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"❌ Errore API master: {resp.text}")
    return resp.json()

def get_slave_positions(slave_base_url):
    url = f"{slave_base_url}/positions"
    log(f"🔹 Recupero posizioni slave: {url}")

    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"❌ Errore API slave: {resp.text}")
    return resp.json()

def close_slave_order(slave_base_url, slave_ticket):
    """
    Chiude un ordine sullo slave server via API REST.
    """
    url = f"{slave_base_url}/close_order/{slave_ticket}"
    log(f"🔹 Chiudo ordine slave {slave_ticket}: {url}")

    try:
        resp = requests.post(url, timeout=10)
    except requests.exceptions.RequestException as e:
        log(f"❌ Errore di rete durante la chiusura dell’ordine {slave_ticket}: {e}")
        return {"error": str(e)}

    if resp.status_code != 200:
        log(f"❌ Errore chiusura ordine {slave_ticket}: {resp.status_code} - {resp.text}")
    else:
        log(f"✅ Ordine {slave_ticket} chiuso correttamente sullo slave")

    try:
        return resp.json()
    except Exception:
        log(f"⚠️ Risposta non in JSON valida per ordine {slave_ticket}: {resp.text}")
        return {"error": "invalid JSON response", "raw": resp.text}

@router.get("/history")
def get_history(
    trader_id: int,
    symbol: str | None = None,
    profit_min: float | None = None,
    profit_max: float | None = None,
):
    return get_history_db(trader_id, symbol, profit_min, profit_max)

def get_history_db(trader_id, symbol=None, profit_min=None, profit_max=None):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT 
            id, trader_id, master_order_id, master_ticket,
            ticket, symbol, type, volume,
            price_open, price_close, profit, comment,
            opened_at, closed_at
        FROM slave_orders
        WHERE trader_id = %s
          AND closed_at IS NOT NULL
    """
    params = [trader_id]

    if symbol:
        query += " AND symbol = %s"
        params.append(symbol)

    if profit_min is not None:
        query += " AND profit >= %s"
        params.append(profit_min)

    if profit_max is not None:
        query += " AND profit <= %s"
        params.append(profit_max)

    query += " ORDER BY closed_at DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows

# manda ordine allo slave, no copytrading
class OrderPayload(BaseModel):
    trader_id: int
    order_type: str = "buy"
    volume: float = 0.10
    symbol: str

@router.post("/traders/{trader_id}/open_order_on_slave")
def open_order_on_slave(payload: OrderPayload):

    order_type = payload.order_type
    volume = payload.volume
    symbol = payload.symbol
    trader_id = payload.trader_id

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)


    log("🚀 Entrato in open_order_on_slave()")

    # 1️⃣ Recupero trader
    trader = get_trader(cursor, trader_id)
    if not trader:
        return {"status": "ko", "message": "Trader non trovato", "logs": logs}

    # 2️⃣ Inizializza server SLAVE
    base_url_slave = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    log(f"🌐 Init MT5 slave: {base_url_slave}")
    ensure_mt5_initialized(base_url_slave, trader["slave_path"], log)

    # 3️⃣ Login SLAVE
    login_url = f"{base_url_slave}/login"
    login_body = {
        "login": int(trader["slave_user"]),
        "password": trader["slave_pwd"],
        "server": trader["slave_name"]
    }

    log("🔐 Login allo SLAVE...")
    resp = requests.post(login_url, json=login_body, timeout=20)
    if resp.status_code != 200:
        return {"status": "ko", "message": f"Errore login slave: {resp.text}", "logs": logs}

    log("✅ Login SLAVE riuscito")

    log(f"Simbolo dal payload è: {symbol}")

    # 4️⃣ Recupero tick SLAVE
    # symbol = trader["symbol"] if "symbol" in trader else "XAUUSD"
    tick_url = f"{base_url_slave}/symbol_tick/{symbol}"


    log(f"📡 Richiedo tick dello SLAVE: {tick_url}")
    resp_tick = requests.get(tick_url, timeout=10)

    if resp_tick.status_code != 200:
        log(f"🔎 Verifica simbolo sullo SLAVE: {symbol}")

        return {"status": "ko", "message": f"Errore tick {resp_tick.text}", "logs": logs}

    tick = resp_tick.json()
    log(f"📈 Tick ricevuto: BID={tick['bid']} ASK={tick['ask']}")

    price = tick["ask"] if order_type.lower() == "buy" else tick["bid"]

    # 5️⃣ Prepara ordine
    order_request = {
        "symbol": symbol,
        "volume": volume * 5,
        "type": order_type.lower(),
        "price": price,
        # "price": 0.0,
        "sl": None,
        "tp": None
        
    }

    order_url = f"{base_url_slave}/order"
    log(f"📤 Invio ordine allo SLAVE → {order_url}")
    log(json.dumps(order_request, indent=2))

    resp_order = requests.post(order_url, json=order_request, timeout=20)
    if resp_order.status_code != 200:
        return {"status": "ko", "message": f"Errore invio ordine: {resp_order.text}", "logs": logs}

    result = resp_order.json()
    log(f"✅ Risposta SLAVE: {result}")

    ticket = result.get("result", {}).get("order")

    # 6️⃣ Scrive nel DB solo slave_orders
    # if ticket:
    #     cursor.execute("""
    #         INSERT INTO slave_orders
    #             (trader_id, master_order_id, master_ticket, ticket,
    #              symbol, type, volume, price_open, opened_at)
    #         VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s, NOW())
    #     """,
    #     (trader_id, ticket, symbol, order_type, volume, price))

    #     conn.commit()
    #     log(f"💾 Ordine SLAVE salvato nel DB. Ticket={ticket}")

    # 7️⃣ Fine
    cursor.close()
    conn.close()

    return {
        "status": "ok",
        "message": "Ordine inviato allo SLAVE",
        "ticket": ticket,
        "logs": logs
    }


# chiusura ordine determinato
class CloseOrderPayload(BaseModel):
    trader_id: int
    symbol: str


@router.post("/traders/{trader_id}/close_order_on_slave")
def close_order_on_slave(payload: CloseOrderPayload):
    trader_id = payload.trader_id
    symbol = payload.symbol

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    log("🚀 Entrato in close_order_on_slave()")

    # 1️⃣ Recupero trader
    trader = get_trader(cursor, trader_id)
    if not trader:
        return {"status": "ko", "message": "Trader non trovato", "logs": logs}

    # 2️⃣ Inizializza server SLAVE
    base_url_slave = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    log(f"🌐 Init MT5 slave: {base_url_slave}")
    ensure_mt5_initialized(base_url_slave, trader["slave_path"], log)

    # 3️⃣ Login SLAVE
    login_url = f"{base_url_slave}/login"
    login_body = {
        "login": int(trader["slave_user"]),
        "password": trader["slave_pwd"],
        "server": trader["slave_name"]
    }

    log("🔐 Login allo SLAVE...")
    resp = requests.post(login_url, json=login_body, timeout=20)
    if resp.status_code != 200:
        return {"status": "ko", "message": f"Errore login slave: {resp.text}", "logs": logs}

    log("✅ Login SLAVE riuscito")
    log(f"Simbolo da chiudere: {symbol}")

    # 4️⃣ Prepara richiesta chiusura ordine
    close_order_url = f"{base_url_slave}/close_order"
    payload_order = {"symbol": symbol}

    log(f"📤 Invio richiesta chiusura ordine → {close_order_url}")
    log(json.dumps(payload_order, indent=2))

    try:
        resp_order = requests.post(close_order_url, json=payload_order, timeout=20)
        if resp_order.status_code != 200:
            return {"status": "ko", "message": f"Errore chiusura ordine: {resp_order.text}", "logs": logs}

        result = resp_order.json()
        log(f"✅ Risposta SLAVE chiusura: {result}")

    except requests.RequestException as e:
        log(f"❌ Errore invio richiesta chiusura ordine: {e}")
        return {"status": "ko", "message": str(e), "logs": logs}

    cursor.close()
    conn.close()

    return {
        "status": "ok",
        "message": f"Ordine {symbol} chiuso sullo SLAVE",
        "result": result,
        "logs": logs
    }

if __name__ == "__main__":
    create_user("roberto", "roberto123")

