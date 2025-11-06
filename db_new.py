from datetime import datetime
from logging import info
from typing import List
import uuid
import mysql.connector
from mysql.connector import Error
import requests
from models import LoginRequest, LoginResponse, ServerRequest, TraderServersUpdate,Trader, Newtrader,UserResponse, ServerResponse
from fastapi import FastAPI, HTTPException
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware
# import MetaTrader5 as mt5
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
    except Error as e:
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


# @router.get("/servers", response_model=List[ServerResponse])
# def get_servers():
#     try:
#         conn = get_connection()
#         if not conn:
#             raise HTTPException(status_code=500, detail="Database connection failed")

#         cursor = conn.cursor(dictionary=True)
#         cursor.execute("SELECT * FROM servers")
#         rows = cursor.fetchall()
#         cursor.close()
#         conn.close()

#         return rows

#     except mysql.connector.Error as db_err:
#         # Errore specifico MySQL
#         detail = f"MySQL error {db_err.errno}: {db_err.msg}"
#         print(f"❌ {detail}")
#         raise HTTPException(status_code=500, detail=detail)

#     except Exception as e:
#         # Qualsiasi altro errore
#         print(f"❌ Errore imprevisto in get_servers: {e}")
#         raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

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


# @router.delete("/servers/{server_id}")
# def delete_server(server_id: int):
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

    except Error as e:
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
# @router.post("/traders/{trader_id}/copy_orders")
# def copy_orders(trader_id: int):
#     conn = get_connection()
#     cursor = conn.cursor(dictionary=True)

#     # 1️⃣ Recupera info del trader (master e slave)
#     cursor.execute("""
#         SELECT t.id, t.name, t.moltiplicatore, t.fix_lot, t.sl, t.tp, t.tsl,
#                ms.server AS master_name, ms.user AS master_user, ms.pwd AS master_pwd,
#                ms.ip AS master_ip, ms.port AS master_port,
#                ss.server AS slave_name, ss.user AS slave_user, ss.pwd AS slave_pwd,
#                ss.ip AS slave_ip, ss.port AS slave_port
#         FROM traders t
#         JOIN servers ms ON ms.id = t.master_server_id
#         JOIN servers ss ON ss.id = t.slave_server_id
#         WHERE t.id = %s
#     """, (trader_id,))
#     trader = cursor.fetchone()

#     if not trader:
#         cursor.close()
#         conn.close()
#         raise HTTPException(status_code=404, detail="Trader non trovato")

#     # Funzione interna per login all'API MT5 locale
#     def mt5_login(ip, port, login, password, server_name):
#         url = f"http://{ip}:{port}/login"
#         payload = {
#             "login": int(login),
#             "password": password,
#             "server": server_name
#         }
#         try:
#             resp = requests.post(url, json=payload, timeout=10)
#             resp.raise_for_status()
#             return True
#         except requests.exceptions.RequestException as e:
#             raise HTTPException(
#                 status_code=500,
#                 detail=f"Errore comunicazione con MT5 API {server_name}: {str(e)}"
#             )

#     # 2️⃣ Login master
#     mt5_login(trader["master_ip"], trader["master_port"], trader["master_user"],
#               trader["master_pwd"], trader["master_name"])
#     print(f"Connessione al master {trader['master_user']} riuscita!")

#     # 3️⃣ Recupera posizioni master
#     master_url = f"http://{trader['master_ip']}:{trader['master_port']}/positions"
#     try:
#         resp = requests.get(master_url, timeout=10)
#         resp.raise_for_status()
#         master_positions = resp.json()
#     except requests.exceptions.RequestException as e:
#         raise HTTPException(status_code=500, detail=f"Errore recupero posizioni master: {str(e)}")

#     if not master_positions:
#         raise HTTPException(status_code=404, detail="Nessuna posizione sul master")

#     # 4️⃣ Login slave
#     mt5_login(trader["slave_ip"], trader["slave_port"], trader["slave_user"],
#               trader["slave_pwd"], trader["slave_name"])
#     print(f"Connessione allo slave {trader['slave_user']} riuscita!")

#     slave_url = f"http://{trader['slave_ip']}:{trader['slave_port']}/order"

#     # 5️⃣ Copia ordini dal master allo slave
#     for pos in master_positions:
#         symbol = pos["symbol"]
#         order_type = "buy" if pos["type"] == 0 else "sell"
#         volume = trader["fix_lot"] or round(pos["volume"] * float(trader["moltiplicatore"]), 2)

#         order_payload = {
#             "action": 0,  # mt5.TRADE_ACTION_DEAL
#             "symbol": symbol,
#             "volume": volume,
#             "type": 0 if order_type == "buy" else 1,  # BUY=0, SELL=1
#             "price": pos.get("price_open", 0),
#             "sl": pos.get("sl", 0),
#             "tp": pos.get("tp", 0),
#             "deviation": 10,
#             "magic": 123456,
#             "comment": f"Copied from master {trader_id}"
#         }

#         try:
#             resp = requests.post(slave_url, json=order_payload, timeout=10)
#             resp.raise_for_status()
#             # puoi registrare l'ordine sul DB qui se vuoi
#             print(f"Ordine copiato su slave: {symbol}")
#         except requests.exceptions.RequestException as e:
#             print(f"⚠️ Errore invio ordine su slave {symbol}: {str(e)}")
#             continue

#     cursor.close()
#     conn.close()
#     return {"message": "Ordini copiati con successo", "trader": trader, "positions_copied": len(master_positions)}



if __name__ == "__main__":
    create_user("roberto", "roberto123")

