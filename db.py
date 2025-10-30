from datetime import datetime
from logging import info
from typing import List
import uuid
import mysql.connector
from mysql.connector import Error
from models import LoginRequest, LoginResponse, ServerRequest, Trader, Newtrader,UserResponse, ServerResponse
from fastapi import FastAPI, HTTPException
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware
import MetaTrader5 as mt5
# from fastapi_utils.tasks import repeat_every

# app = FastAPI()
router = APIRouter()



import bcrypt
import os

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

@router.get("/servers", response_model=List[ServerResponse])
def get_servers():
    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM servers")
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return rows

@router.post("/servers")
def insert_server(server: ServerRequest):

    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        query = """
            INSERT INTO servers 
            (user, pwd, server, platform, ip, port, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """

        values = (
            server.user,
            server.pwd,
            server.server,
            server.platform,
            server.ip,
            server.port,
            server.is_active
        )


        cursor.execute(query, values)
        conn.commit()

        new_id = cursor.lastrowid

        cursor.close()
        conn.close()

        return {"message": "Server added successfully", "id": new_id}

    except Error as e:
        print(f"Errore DB: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

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


# @router.post("/traders/{trader_id}/copy_orders")
# def copy_orders(trader_id: int):

#     print(f"Trader ID passato: {trader_id}")  # log su console

#     conn = get_connection()
#     cursor = conn.cursor(dictionary=True)

#     # 1️⃣ Recupera info del trader (master e slave)
#     cursor.execute("""
#         SELECT t.id, t.moltiplicatore, t.fix_lot, t.sl, t.tp, t.tsl,
#                ms.server AS master_name, ms.user AS master_user, ms.pwd AS master_pwd,
#                ss.server AS slave_name, ss.user AS slave_user, ss.pwd AS slave_pwd
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

#     # 2️⃣ Connessione al master MT5
#     if not mt5.initialize(trader["master_name"], login=int(trader["master_user"]), password=trader["master_pwd"]):
#         cursor.close()
#         conn.close()
#         raise HTTPException(status_code=500, detail="Connessione al master fallita")

#     master_positions = mt5.positions_get()
#     if not master_positions:
#         mt5.shutdown()
#         cursor.close()
#         conn.close()
#         raise HTTPException(status_code=404, detail="Nessuna posizione sul master")

#     # 3️⃣ Connessione allo slave MT5
#     mt5.shutdown()
#     if not mt5.initialize(trader["slave_name"], login=int(trader["slave_user"]), password=trader["slave_pwd"]):
#         cursor.close()
#         conn.close()
#         raise HTTPException(status_code=500, detail="Connessione allo slave fallita")

#     # 4️⃣ Copia ogni ordine master sullo slave
#     for pos in master_positions:
#         symbol = pos.symbol
#         order_type = "buy" if pos.type == 0 else "sell"
#         volume = trader["fix_lot"] or round(pos.volume * float(trader["moltiplicatore"]), 2)

#         request = {
#             "action": mt5.TRADE_ACTION_DEAL,
#             "symbol": symbol,
#             "volume": volume,
#             "type": mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL,
#             "price": mt5.symbol_info_tick(symbol).ask if order_type == "buy" else mt5.symbol_info_tick(symbol).bid,
#             "sl": pos.sl,
#             "tp": pos.tp,
#             "deviation": 10,
#             "magic": 123456,
#             "comment": f"Copied from master {trader_id}",
#             "type_time": mt5.ORDER_TIME_GTC,
#             "type_filling": mt5.ORDER_FILLING_IOC,
#         }

#         result = mt5.order_send(request)
#         if result.retcode != mt5.TRADE_RETCODE_DONE:
#             print(f"Errore copia {symbol}: {result.comment}")
#             continue

#         slave_ticket = result.order

#         # Inserisci nel DB master_orders + slave_orders
#         cursor.execute("""
#             INSERT INTO master_orders (trader_id, ticket, symbol, type, volume, price_open, sl, tp, opened_at)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """, (
#             trader_id, pos.ticket, symbol, order_type, pos.volume,
#             pos.price_open, pos.sl, pos.tp, datetime.fromtimestamp(pos.time)
#         ))
#         master_order_id = cursor.lastrowid

#         cursor.execute("""
#             INSERT INTO slave_orders (trader_id, master_order_id, ticket, symbol, type, volume, price_open, sl, tp, opened_at)
#             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
#         """, (
#             trader_id, master_order_id, slave_ticket, symbol, order_type, volume,
#             request["price"], pos.sl, pos.tp
#         ))

#     # 5️⃣ Commit e chiusura
#     conn.commit()
#     mt5.shutdown()
#     cursor.close()
#     conn.close()

#     return {"status": "ok", "message": "Ordini copiati con successo"}

@router.post("/traders/{trader_id}/copy_orders")
def copy_orders(trader_id: int):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    # print(f"🚀 entrato BE")

    # 1️⃣ Recupera info del trader (master e slave)
    cursor.execute("""
        SELECT t.id, t.name, t.moltiplicatore, t.fix_lot, t.sl, t.tp, t.tsl,
               ms.server AS master_name, ms.user AS master_user, ms.pwd AS master_pwd,
               ss.server AS slave_name, ss.user AS slave_user, ss.pwd AS slave_pwd
        FROM traders t
        JOIN servers ms ON ms.id = t.master_server_id
        JOIN servers ss ON ss.id = t.slave_server_id
        WHERE t.id = %s
    """, (trader_id,))
    trader = cursor.fetchone()
        # 👇 Stampa in console backend
    print("=== Trader Info ===")
    print(trader)
    print("===================")
    print(trader["master_name"])
    print(trader["master_user"])
    print(trader["master_pwd"])


    if not trader:
        # conn.close()

        raise HTTPException(status_code=404, detail="Trader non trovato")
    
    # 2️⃣ Connessione al master MT5
    if not mt5.initialize(
        path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
        login=int(trader["master_user"]),
        password=trader["master_pwd"],
        server=trader["master_name"]
    ):
        last_err = mt5.last_error()
        conn.close()
        raise HTTPException(
            status_code=500,
            detail=f"Connessione al master fallita: {last_err}"
    )
    print(f"Connessione al master {trader['master_user']} riuscita!")

    master_positions = mt5.positions_get()
    if not master_positions:
        mt5.shutdown()
        cursor.close()
        conn.close()
        print(f"Nessuna posizione sul master")
        raise HTTPException(status_code=404, detail="Nessuna posizione sul master")

    # Stampa tutte le posizioni trovate
    print("=== POSIZIONI SUL MASTER ===")
    for pos in master_positions:
        print(f"[MASTER] {pos._asdict()}")  # aggiunge il tag [MASTER] davanti ai dettagli



    # # 3️⃣ Connessione allo slave MT5
    if not mt5.initialize(
        path=r"C:\Program Files\MetaTrader 5\terminal64.exe",
        login=int(trader["slave_user"]),
        password=trader["slave_pwd"],
        server=trader["slave_name"]
    ):
        last_err = mt5.last_error()
        conn.close()
        raise HTTPException(
            status_code=500,
            detail=f"Connessione al server fallita: {last_err}"
    )
    print(f"Connessione allo slave {trader['slave_user']} riuscita!")


    # 4️⃣ Copia ogni ordine master sullo slave
    import traceback

    for pos in master_positions:
        try:
            symbol = pos.symbol
            order_type = "buy" if pos.type == 0 else "sell"
            volume = trader["fix_lot"] or round(pos.volume * float(trader["moltiplicatore"]), 2)
            print(f"Master symbol: {symbol}, tipo: {order_type}, volume calcolato per slave: {volume}")

            # 🔹 1️⃣ Controllo se il simbolo è disponibile e visibile sullo slave
            sym_info = mt5.symbol_info(symbol)
            if sym_info is None:
                print(f"⚠️ Simbolo {symbol} non trovato sullo slave.")
                continue


            if not sym_info.visible:
                print(f"🔹 Simbolo {symbol} non visibile. Provo ad abilitarlo...")
                if not mt5.symbol_select(symbol, True):
                    print(f"❌ Errore: impossibile attivare {symbol} sullo slave.")
                    continue
                else:
                    print(f"✅ Simbolo {symbol} attivato con successo sullo slave.")

            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                print(f"⚠️ Nessun tick disponibile per {symbol} (probabile simbolo non visibile nel Market Watch)")
                continue
            
            sym_info = mt5.symbol_info(symbol)
            info = mt5.symbol_info(symbol)
            # print(f"Symbol info for {symbol}:")
            # print(f"  filling_mode: {info.filling_mode}")
            # print(f"  trade_mode: {info.trade_mode}")
            # print(f"  trade_exemode: {info.trade_exemode}")

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY if order_type == "buy" else mt5.ORDER_TYPE_SELL,
                "price": tick.ask if order_type == "buy" else tick.bid,
                "sl": pos.sl,
                "tp": pos.tp,
                "deviation": 10,
                "magic": 123456,
                "comment": f"Copied from master {trader_id}",
                "type_time": mt5.ORDER_TIME_GTC,
                # "type_filling": filling_mode, dà errore..
            }

            print(f"🔁 Invio ordine su slave: {request}")
            result = mt5.order_send(request)

            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                # 🔹 Inserimento nel DB master_orders
                cursor.execute("""
                    INSERT INTO master_orders (trader_id, ticket, symbol, type, volume, price_open, sl, tp, opened_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    trader_id, pos.ticket, symbol, order_type, pos.volume,
                    pos.price_open, pos.sl, pos.tp, datetime.fromtimestamp(pos.time)
                ))
                master_order_id = cursor.lastrowid

                # 🔹 Inserimento nel DB slave_orders
                cursor.execute("""
                    INSERT INTO slave_orders (trader_id, master_order_id, ticket, symbol, type, volume, price_open, sl, tp, opened_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    trader_id, master_order_id, result.order, symbol, order_type, volume,
                    request["price"], pos.sl, pos.tp
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
    return {"message": "Trader info retrieved", "trader": trader}




if __name__ == "__main__":
    create_user("roberto", "roberto123")

