from datetime import datetime
import json
from logging import info
from typing import List
import uuid
import mysql.connector
# from mysql.connector import Error
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

# from fastapi_utils.tasks import repeat_every

router = APIRouter()

logs = []  # elenco dei messaggi di log

start_time = datetime.now()  
# funz messaggistica di log
def log(message: str):
        """Aggiunge un messaggio con timestamp relativo."""
        elapsed = (datetime.now() - start_time).total_seconds()
        timestamp = f"[+{elapsed:.1f}s]"
        logs.append(f"{timestamp} {message}")
        print(f"{timestamp} {message}")  # Mantieni anche la stampa in console


def get_connection():
    try:
        
        conn = mysql.connector.connect(
            host=os.environ.get("MYSQL_HOST", "192.168.1.208"),
            user=os.environ.get("MYSQL_USER", "trader"),
            password="vibe2025",
            database=os.environ.get("MYSQL_DB", "trader_db"),
            port=int(os.environ.get("MYSQL_PORT", 3306))  # opzionale
        )

        # db locale 
        # conn = mysql.connector.connect(
        #     host="127.0.0.1",       # o "127.0.0.1"
        #     user="trader",            # utente MySQL locale
        #     password="vibe2025",            # lascia vuoto se non hai password
        #     database="trader_db",   # nome del tuo database
        #     port=3306               # porta predefinita MySQL
        # )


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


def insert_order(symbol, lot, sl, tp, magic, comment):
    conn = get_connection()
    if not conn:
        return False
    cursor = conn.cursor()
    query = """
        INSERT INTO orders (symbol, lot, sl, tp, magic, comment)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    cursor.execute(query, (symbol, lot, sl, tp, magic, comment))
    conn.commit()
    cursor.close()
    conn.close()
    return True

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
        # cursor.execute("SELECT id FROM servers WHERE id = %s", (server_id,))
        cursor.execute("SELECT id FROM servers WHERE id = %s", (server_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Server not found")

        # Cancella il server
        # cursor.execute("DELETE FROM servers WHERE id = %s", (server_id,))
        cursor.execute("DELETE FROM servers2 WHERE id = %s", (server_id,))
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

# @router.put("/traders/{trader_id}/servers")
# def update_trader_servers(trader_id: int, update: TraderServersUpdate):
#     conn = get_connection()
#     cursor = conn.cursor(dictionary=True)

#     # Controlla se il trader esiste
#     cursor.execute("SELECT * FROM traders WHERE id = %s", (trader_id,))
#     trader = cursor.fetchone()
#     if not trader:
#         cursor.close()
#         conn.close()
#         raise HTTPException(status_code=404, detail="Trader not found")

#     # Aggiorna i server
#     cursor.execute("""
#         UPDATE traders
#         SET master_server_id = %s,
#             slave_server_id = %s
#         WHERE id = %s
#     """, (update.master_server_id, update.slave_server_id, trader_id))
    
#     conn.commit()

#     # Recupera il trader aggiornato
#     cursor.execute("SELECT * FROM traders WHERE id = %s", (trader_id,))
#     updated_trader = cursor.fetchone()

#     cursor.close()
#     conn.close()
#     return updated_trader

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

    start_time = datetime.now()  

    def log(message: str):
        """Aggiunge un messaggio con timestamp relativo."""
        elapsed = (datetime.now() - start_time).total_seconds()
        timestamp = f"[+{elapsed:.1f}s]"
        logs.append(f"{timestamp} {message}")
        print(f"{timestamp} {message}")  # Mantieni anche la stampa in console


    log("🚀 Entrato in copy_orders()")

    cursor = conn.cursor(dictionary=True)

    # # 1️⃣ Recupera info del trader (master e slave)
    trader = get_trader(cursor, trader_id)

    if not trader:
        
        return {"status": "ko", "message": "Trader non trovato", "logs": logs}

    
    """
    Inizializza e connette il master MT5 remoto tramite API HTTP.
    """

    base_url = f"http://{trader['master_ip']}:{trader['master_port']}"

    # 1️⃣ Inizializza terminale remoto
    init_url = f"{base_url}/init-mt5"
    init_body = {"path": trader["master_path"]}
    health_url = f"{base_url}/health"

    log(f"🔍 Verifico stato terminale remoto su {health_url}...")
    # try:
    health_resp = requests.get(health_url, timeout=5)
    if health_resp.status_code == 200:
        health_data = health_resp.json()
        if health_data.get("status") == "ok":
            log(f"✅ MT5 già inizializzato (versione {health_data.get('mt5_version')})")
        else:
            # raise Exception("MT5 non inizializzato, serve init")
            log(f"🔹 Inizializzo terminale remoto {init_url}")
            resp = requests.post(init_url, json=init_body, timeout=10)
            if resp.status_code != 200:
                raise Exception(f"❌ Init fallita su {base_url}: {resp.text}")

            log(f"✅ Init OK su {base_url}")

        
    # 2️⃣ Login remoto a master
    login_url = f"{base_url}/login"
    login_body = {
        "login": int(trader["master_user"]),
        "password": trader["master_pwd"],
        "server": trader["master_name"]
    }

    log(f"🔹 Connessione al master via {login_url}")
    resp = requests.post(login_url, json=login_body, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"❌ Login fallito su {base_url}: {resp.text}")

    data = resp.json()
    log(f"✅ Connessione al master {trader['master_user']} riuscita! Bilancio: {data.get('balance')}")

    # URL base del server master
    base_url = f"http://{trader['master_ip']}:{trader['master_port']}"

    try:
        positions_url = f"{base_url}/positions"
        log(f"🔹 Recupero posizioni dal master via {positions_url}")

        resp = requests.get(positions_url, timeout=10)
        if resp.status_code != 200:
            log(f"❌ Errore API master: {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Errore dal master API: {resp.text}")

        master_positions = resp.json()
        # master_positions = data.get("positions", [])

        if not master_positions:
            log("⚠️ Nessuna posizione aperta sul master.")
            # return {"status": "ko", "message": "Errore login master", "logs": logs}

            # raise HTTPException(status_code=404, detail="Nessuna posizione sul master")

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

    base_url = f"http://{trader['slave_ip']}:{trader['slave_port']}"

    # 1️⃣ Inizializza terminale remoto
    init_url = f"{base_url}/init-mt5"
    init_body = {"path": trader["slave_path"]}
    health_url = f"{base_url}/health"

    log(f"🔍 Verifico stato terminale remoto su {health_url}...")
    # try:
    health_resp = requests.get(health_url, timeout=5)
    if health_resp.status_code == 200:
        health_data = health_resp.json()
        if health_data.get("status") == "ok":
            log(f"✅ MT5 già inizializzato (versione {health_data.get('mt5_version')})")
            # ---prova altrimenti va in errore !
            log(f"🔹 Inizializzo terminale remoto {init_url}")
            resp = requests.post(init_url, json=init_body, timeout=30)
        else:
            # raise Exception("MT5 non inizializzato, serve init")
            log(f"🔹 Inizializzo terminale remoto {init_url}")
            resp = requests.post(init_url, json=init_body, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"❌ Init fallita su {base_url}: {resp.text}")

            log(f"✅ Init OK su {base_url}")
    

    # 2️⃣ Login remoto a slave
    login_url = f"{base_url}/login"
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
        raise Exception(f"❌ Login fallito su {base_url}: {resp.text}")

    data = resp.json()
    log(f"✅ Connessione allo slave {trader['slave_user']} riuscita! Bilancio: {data.get('balance')}")


    # 4️⃣ Copia ogni ordine master sullo slave
    import traceback

    base_url = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    
    for pos in master_positions:
        try:
            symbol = pos["symbol"]
            order_type = "buy" if pos["type"] == 0 else "sell"
            volume = trader["fix_lot"] or round(pos["volume"] * float(trader["moltiplicatore"]), 2)
            log(f"Master symbol: {symbol}, tipo: {order_type}, volume calcolato per slave: {volume}")

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

            if sl_pips and sl_pips > 0:
                pip_value = sym_info.get("point")  # valore del singolo punto del simbolo
                sl_distance = sl_pips * pip_value

                if order_type == "buy":
                    # SL sotto il prezzo ask
                    calculated_sl = tick["ask"] - sl_distance
                else:
                    # SL sopra il prezzo bid (per SELL)
                    calculated_sl = tick["bid"] + sl_distance
            else:
                calculated_sl = None  # se sl=0 non impostare
        
            # 🔹 3️⃣ Preparo la richiesta da inviare allo slave
            request = {
                "symbol": symbol,
                "volume": volume,
                "type": "buy" if order_type == "buy" else "sell",
                "price": tick["ask"] if order_type == "buy" else tick["bid"],
                "sl": calculated_sl,
                "tp": pos["tp"],
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
                cursor.execute("""
                    INSERT INTO master_orders (trader_id, ticket, symbol, type, volume, price_open, sl, tp, opened_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    # trader_id, pos.ticket, symbol, order_type, pos.volume,
                    # pos.price_open, pos.sl, pos.tp, datetime.fromtimestamp(pos.time)

                    trader_id, pos.get("ticket"), symbol, order_type, pos.get("volume"),
                    pos.get("price_open"), pos.get("sl"), pos.get("tp"),
                    datetime.fromtimestamp(pos.get("time"))
                ))
                master_order_id = cursor.lastrowid

                # 🔹 Inserimento nel DB slave_orders
                cursor.execute("""
                    INSERT INTO slave_orders (trader_id, master_order_id, ticket, symbol, type, volume, price_open, sl, tp, opened_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    trader_id, master_order_id, result.get("result", {}).get("order"), symbol, order_type, volume, request["price"], pos.get("sl"), pos.get("tp")
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

        # 2️⃣ Recupero posizioni slave
        slave_positions = get_slave_positions(slave_base)
        log(f"✅ Ticket slave attivi: {[sp['ticket'] for sp in slave_positions]}")

        # 3️⃣ Chiudo ordini mancanti
        for sp in slave_positions:
            slave_ticket = sp["ticket"]

            if slave_ticket not in master_tickets:
                log(f"⚠️ Ticket {slave_ticket} sullo slave NON presente sul master → chiudo")

                # Chiudi via API slave
                resp = close_slave_order(slave_base, slave_ticket)

                # Aggiorna DB
                cursor.execute(
                    "UPDATE slave_orders SET closed_at=NOW() WHERE ticket=%s",
                    (slave_ticket,)
                )
                conn.commit()

                log(f"✅ Ticket {slave_ticket} chiuso e DB aggiornato")

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


# sincronizza l'eventuale chiusura di una posizione sul master con lo slave
@router.post("/traders/{trader_id}/sync_close")
def sync_close(trader_id: int):
    logs = []
    start_time = datetime.now()
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Recupero trader
    trader = get_trader(cursor, trader_id)
    if not trader:
        return {"status":"ko","message":"Trader non trovato","logs":logs}

    master_base = f"http://{trader['master_ip']}:{trader['master_port']}"
    slave_base  = f"http://{trader['slave_ip']}:{trader['slave_port']}"

    master_positions = get_master_positions(master_base)
    slave_positions  = get_slave_positions(slave_base)

    # Lista dei ticket aperti sul master
    master_tickets = [p["ticket"] for p in master_positions]

    # Ciclo sulle posizioni slave
    for sp in slave_positions:
        slave_ticket = sp["ticket"]
        if slave_ticket not in master_tickets:
            # log(logs, start_time, f"⚠️ Posizione {slave_ticket} sullo slave non esiste più sul master, chiudo...")
            close_slave_order(slave_base, slave_ticket)

            # Aggiorna DB se vuoi tenere traccia della chiusura
            cursor.execute("UPDATE slave_orders SET closed_at=NOW() WHERE ticket=%s", (slave_ticket,))
            conn.commit()

    cursor.close()
    conn.close()

    return {"status": "ok", "message": "Sincronizzazione completata", "logs": logs}




if __name__ == "__main__":
    create_user("roberto", "roberto123")

