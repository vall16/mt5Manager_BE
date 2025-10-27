from datetime import datetime
from typing import List
import uuid
import mysql.connector
from mysql.connector import Error
from models import LoginRequest, LoginResponse, ServerRequest, Trader, Newtrader,UserResponse, ServerResponse
from fastapi import FastAPI, HTTPException
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware

# app = FastAPI()
router = APIRouter()


import bcrypt
import os

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


if __name__ == "__main__":
    create_user("roberto", "roberto123")

