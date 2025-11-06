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

# from fastapi_utils.tasks import repeat_every

router = APIRouter()

def get_connection():
    try:
        
        # conn = mysql.connector.connect(
        #     host=os.environ.get("MYSQL_HOST", "192.168.1.208"),
        #     user=os.environ.get("MYSQL_USER", "trader"),
        #     password="vibe2025",
        #     database=os.environ.get("MYSQL_DB", "trader_db"),
        #     port=int(os.environ.get("MYSQL_PORT", 3306))  # opzionale
        # )

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
        cursor.execute("SELECT * FROM servers2")
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

# @router.post("/servers")
# def insert_server(server: ServerRequest):

#     conn = get_connection()
#     if not conn:
#         raise HTTPException(status_code=500, detail="Database connection failed")

#     try:
#         cursor = conn.cursor()

#         print("Dati ricevuti:", server.dict())
#         # print("Query:", query)
#         print("Values:", values)


#         query = """
#             INSERT INTO servers 
#             (user, pwd, server, platform, ip, path, port, is_active, created_at, updated_at)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s,NOW(), NOW())
#         """

#         values = (
#             server.user,
#             server.pwd,
#             server.server,
#             server.platform,
#             server.ip,
#             server.path,
#             server.port,
#             server.is_active
#         )


#         cursor.execute(query, values)
#         conn.commit()

#         new_id = cursor.lastrowid

#         cursor.close()
#         conn.close()

#         return {"message": "Server added successfully", "id": new_id}

#     except Error as e:
#         print(f"Errore DB: {e}")
#         raise HTTPException(status_code=500, detail=f"Database error: {e}")

# @router.post("/servers")
# def insert_server(server: ServerRequest):

#     conn = get_connection()
#     if not conn:
#         raise HTTPException(status_code=500, detail="Database connection failed")

#     try:
#         cursor = conn.cursor()

#         # --- Preparazione ---
#         query = """
#             INSERT INTO servers 
#             (`user`, `pwd`, `server`, `platform`, `ip`, `path`, `port`, `is_active`, `created_at`, `updated_at`)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
#         """

#         values = (
#             server.user,
#             server.pwd,
#             server.server,
#             server.platform,
#             server.ip,
#             server.path,
#             server.port,
#             server.is_active
#         )

#         print("\n🧩 [DEBUG SQL] Tentativo di INSERT su 'servers' ...")
#         print("Query SQL:", query)
#         print("Valori:", values)

#         # --- Esecuzione ---
#         cursor.execute(query, values)
#         conn.commit()

#         new_id = cursor.lastrowid
#         print(f"✅ [OK] Inserito record servers.id={new_id}")

#         cursor.close()
#         conn.close()

#         return {"message": "Server added successfully", "id": new_id}

#     except Exception as e:
#         import traceback
#         print("\n❌ [ERRORE SQL]")
#         print("Tipo errore:", type(e).__name__)
#         print("Dettaglio:", str(e))
#         traceback.print_exc()
#         try:
#             conn.rollback()
#         except:
#             pass
#         raise HTTPException(status_code=500, detail=f"Errore SQL: {e}")

# from mysql.connector import Error as MySQLError

@router.post("/servers")
def insert_server(server: ServerRequest):

    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        query = """
            # INSERT INTO servers 
            INSERT INTO servers2 
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
        cursor.execute("SELECT id FROM servers2 WHERE id = %s", (server_id,))
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

@router.put("/traders/{trader_id}/servers")
def update_trader_servers(trader_id: int, update: TraderServersUpdate):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # Controlla se il trader esiste
    cursor.execute("SELECT * FROM traders WHERE id = %s", (trader_id,))
    trader = cursor.fetchone()
    if not trader:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Trader not found")

    # Aggiorna i server
    cursor.execute("""
        UPDATE traders
        SET master_server_id = %s,
            slave_server_id = %s
        WHERE id = %s
    """, (update.master_server_id, update.slave_server_id, trader_id))
    
    conn.commit()

    # Recupera il trader aggiornato
    cursor.execute("SELECT * FROM traders WHERE id = %s", (trader_id,))
    updated_trader = cursor.fetchone()

    cursor.close()
    conn.close()
    return updated_trader

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


    # print(f"🚀 entrato BE")
    log("🚀 Entrato in copy_orders()")


    # 1️⃣ Recupera info del trader (master e slave)
    cursor.execute("""
        
        SELECT t.id, t.name, t.moltiplicatore, t.fix_lot, t.sl, t.tp, t.tsl,
       ms.server AS master_name, ms.user AS master_user, ms.pwd AS master_pwd, ms.path AS master_path, ms.ip AS master_ip, ms.port AS master_port,
       ss.server AS slave_name, ss.user AS slave_user, ss.pwd AS slave_pwd, ss.path AS slave_path, ss.ip AS slave_ip, ss.port AS slave_port
            FROM traders t
            JOIN servers2 ms ON ms.id = t.master_server_id
            JOIN servers2 ss ON ss.id = t.slave_server_id
        WHERE t.id = %s;

    """, (trader_id,))
    trader = cursor.fetchone()
        # 👇 Stampa in console backend
    log("=== Trader Info ===")
    log(trader)
    print("===================")
    print(trader["master_name"])
    print(trader["master_user"])
    print(trader["master_pwd"])
    print(trader["master_ip"])
    print(trader["master_port"])


    if not trader:
        # conn.close()

        raise HTTPException(status_code=404, detail="Trader non trovato")
    
    # 2️⃣ Connessione al master MT5
    # if not mt5.initialize(
    #     path=trader["master_path"],
    #     login=int(trader["master_user"]),
    #     password=trader["master_pwd"],
    #     server=trader["master_name"]
    # ):
    #     last_err = mt5.last_error()
    #     conn.close()
    #     raise HTTPException(
    #         status_code=500,
    #         detail=f"Connessione al master fallita: {last_err}"
    # )
    # print(f"Connessione al master {trader['master_user']} riuscita!")

    """
    Inizializza e connette il master MT5 remoto tramite API HTTP.
    """

    base_url = f"http://{trader['master_ip']}:{trader['master_port']}"

    # 1️⃣ Inizializza terminale remoto
    init_url = f"{base_url}/init-mt5"
    init_body = {"path": trader["master_path"]}
    health_url = f"{base_url}/health"

    print(f"🔍 Verifico stato terminale remoto su {health_url}...")
    # try:
    health_resp = requests.get(health_url, timeout=5)
    if health_resp.status_code == 200:
        health_data = health_resp.json()
        if health_data.get("status") == "ok":
            print(f"✅ MT5 già inizializzato (versione {health_data.get('mt5_version')})")
        else:
            # raise Exception("MT5 non inizializzato, serve init")
            print(f"🔹 Inizializzo terminale remoto {init_url}")
            resp = requests.post(init_url, json=init_body, timeout=10)
            if resp.status_code != 200:
                raise Exception(f"❌ Init fallita su {base_url}: {resp.text}")

            print(f"✅ Init OK su {base_url}")

        
    # 2️⃣ Login remoto a master
    login_url = f"{base_url}/login"
    login_body = {
        "login": int(trader["master_user"]),
        "password": trader["master_pwd"],
        "server": trader["master_name"]
    }

    print(f"🔹 Connessione al master via {login_url}")
    resp = requests.post(login_url, json=login_body, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"❌ Login fallito su {base_url}: {resp.text}")

    data = resp.json()
    print(f"✅ Connessione al master {trader['master_user']} riuscita! Bilancio: {data.get('balance')}")

    # URL base del server master
    base_url = f"http://{trader['master_ip']}:{trader['master_port']}"

    try:
        positions_url = f"{base_url}/positions"
        print(f"🔹 Recupero posizioni dal master via {positions_url}")

        resp = requests.get(positions_url, timeout=10)
        if resp.status_code != 200:
            print(f"❌ Errore API master: {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Errore dal master API: {resp.text}")

        master_positions = resp.json()
        # master_positions = data.get("positions", [])

        if not master_positions:
            print("⚠️ Nessuna posizione aperta sul master.")
            raise HTTPException(status_code=404, detail="Nessuna posizione sul master")

        print(f"✅ Posizioni master ricevute: {len(master_positions)}")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Errore di connessione al master API: {e}")
        raise HTTPException(status_code=500, detail=f"Errore connessione al master: {str(e)}")


    # Stampa tutte le posizioni trovate
    print("=== POSIZIONI SUL MASTER ===")
    for pos in master_positions:
        print(f"[MASTER] {pos}")  # pos è già un dict


    # # 3️⃣ Connessione allo slave MT5
    """
    Inizializza e connette il slave MT5 remoto tramite API HTTP.
    """

    base_url = f"http://{trader['slave_ip']}:{trader['slave_port']}"

    # 1️⃣ Inizializza terminale remoto
    init_url = f"{base_url}/init-mt5"
    init_body = {"path": trader["slave_path"]}
    health_url = f"{base_url}/health"

    print(f"🔍 Verifico stato terminale remoto su {health_url}...")
    # try:
    health_resp = requests.get(health_url, timeout=5)
    if health_resp.status_code == 200:
        health_data = health_resp.json()
        if health_data.get("status") == "ok":
            print(f"✅ MT5 già inizializzato (versione {health_data.get('mt5_version')})")
            # ---prova altrimenti va in errore !
            print(f"🔹 Inizializzo terminale remoto {init_url}")
            resp = requests.post(init_url, json=init_body, timeout=30)
        else:
            # raise Exception("MT5 non inizializzato, serve init")
            print(f"🔹 Inizializzo terminale remoto {init_url}")
            resp = requests.post(init_url, json=init_body, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"❌ Init fallita su {base_url}: {resp.text}")

            print(f"✅ Init OK su {base_url}")
    

    # 2️⃣ Login remoto a slave
    login_url = f"{base_url}/login"
    login_body = {
        "login": int(trader["slave_user"]),
        "password": trader["slave_pwd"],
        "server": trader["slave_name"]
    }
    print("=" * 80)
    print("🔹 Tentativo di connessione allo SLAVE")
    print(f"🌐 URL login: {login_url}")
    print(f"👤 Login data:")
    print(f"   - Login ID: {trader['slave_user']}")
    print(f"   - Password: {trader['slave_pwd']}")
    print(f"   - Server:   {trader['slave_name']}")
    print("=" * 80)


    print(f"🔹 Connessione allo slave via {login_url}")
    try:
        resp = requests.post(login_url, json=login_body, timeout=30)
        print(f"📡 Status code: {resp.status_code}")
        print(f"📩 Response: {resp.text}")
    except Exception as e:
        print(f"❌ Errore chiamata login slave: {e}")
        raise

    if resp.status_code != 200:
        raise Exception(f"❌ Login fallito su {base_url}: {resp.text}")

    data = resp.json()
    print(f"✅ Connessione allo slave {trader['slave_user']} riuscita! Bilancio: {data.get('balance')}")


    # 4️⃣ Copia ogni ordine master sullo slave
    import traceback

    base_url = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    
    for pos in master_positions:
        try:
            symbol = pos["symbol"]
            order_type = "buy" if pos["type"] == 0 else "sell"
            volume = trader["fix_lot"] or round(pos["volume"] * float(trader["moltiplicatore"]), 2)
            print(f"Master symbol: {symbol}, tipo: {order_type}, volume calcolato per slave: {volume}")

            # 🔹 1️⃣ Controllo se il simbolo è disponibile e visibile sullo slave
            info_url = f"{base_url}/symbol_info/{symbol}"
            print(f"🔍 Richiedo info simbolo allo slave: {info_url}")
            
            resp = requests.get(info_url, timeout=10)

            sym_info = resp.json()

            if resp.status_code != 200:
                print(f"⚠️ Impossibile ottenere info per {symbol} dallo slave: {resp.text}")
                continue

                
            if not sym_info.get("visible", False):
                print(f"🔹 Simbolo {symbol} non visibile. Provo ad abilitarlo...")
                if not mt5.symbol_select(symbol, True):
                    print(f"❌ Errore: impossibile attivare {symbol} sullo slave.")
                    continue
                else:
                    print(f"✅ Simbolo {symbol} attivato con successo sullo slave.")
            else:
                print(f"✅ Simbolo {symbol} è già visibile sullo slave.")

            # tick = mt5.symbol_info_tick(symbol)
            # if not tick:
            #     print(f"⚠️ Nessun tick disponibile per {symbol} (probabile simbolo non visibile nel Market Watch)")
            #     continue

            # 🔹 2️⃣ Recupero tick dal server slave via API
            tick_url = f"{base_url}/symbol_tick/{symbol}"
            print(f"📡 Richiedo tick allo slave: {tick_url}")

            
            resp_tick = requests.get(tick_url, timeout=10)
            if resp_tick.status_code != 200:
                print(f"⚠️ Nessun tick disponibile per {symbol} dallo slave: {resp_tick.text}")
                continue

            tick = resp_tick.json()
            if not tick or "bid" not in tick or "ask" not in tick:
                print(f"⚠️ Tick incompleto o non valido per {symbol}: {tick}")
                continue

            print(f"✅ Tick ricevuto per {symbol}: bid={tick['bid']}, ask={tick['ask']}")

        
            # 🔹 3️⃣ Preparo la richiesta da inviare allo slave
            request = {
                "symbol": symbol,
                "volume": volume,
                "type": "buy" if order_type == "buy" else "sell",
                "price": tick["ask"] if order_type == "buy" else tick["bid"],
                "sl": pos["sl"],
                "tp": pos["tp"],
                "comment": f"Copied from master {trader_id}",
            }

            # 🔹 3️⃣ Invio ordine allo slave via API
            order_url = f"{base_url}/order"
            print(f"🔁 Invio ordine allo slave via API: {order_url}")
            print(f"🧾 Dati inviati: {json.dumps(request, indent=2)}")

            try:
                resp_order = requests.post(order_url, json=request, timeout=20)

                if resp_order.status_code != 200:
                    print(f"❌ Errore invio ordine allo slave: {resp_order.text}")
                    continue

                result = resp_order.json()
                print(f"✅ Risposta dallo slave: {result}")

            except requests.exceptions.RequestException as e:
                print(f"⚠️ Errore di connessione con lo slave: {e}")
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
                print(f"✅ Ordine copiato e registrato: {symbol}")



        except Exception as e:
            print("❌ Eccezione durante la copia ordine:")
            print(traceback.format_exc())
        continue

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





if __name__ == "__main__":
    create_user("roberto", "roberto123")

