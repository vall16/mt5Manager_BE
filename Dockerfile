# --- 1. Base image ---
FROM python:3.12-slim

# --- 2. Imposta working directory ---
WORKDIR /app

# --- 3. Copia requirements ---
COPY requirements.txt .

# --- 4. Installa dipendenze ---
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# --- 5. Copia tutto il progetto ---
COPY . .

# --- 6. Espone la porta 8080 ---
EXPOSE 8080

# --- 7. Comando di avvio ---
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
