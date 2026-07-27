import mysql.connector

conn = mysql.connector.connect(
    host="localhost",
    user="trader",
    password="vibe2025",
    database="trader_db",
    connection_timeout=5
)
c = conn.cursor()

try:
    c.execute("ALTER TABLE traders ADD COLUMN sessions_filter VARCHAR(255) DEFAULT 'ASIA,LONDON,NY-LON,NY,OFF'")
    print("OK: sessions_filter aggiunta")
except mysql.connector.errors.ProgrammingError as e:
    if "Duplicate column" in str(e):
        print("sessions_filter esiste gia")
    else:
        raise

conn.commit()
conn.close()
