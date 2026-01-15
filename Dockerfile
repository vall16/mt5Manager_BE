# 1️⃣ Usa Python 3.11.9 come base
FROM python:3.11.9-slim

# 2️⃣ Installa strumenti di compilazione e headers Python
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libffi-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 3️⃣ Imposta la working directory dentro il container
WORKDIR /code

# 4️⃣ Copia requirements.txt e installa dipendenze
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 5️⃣ Copia tutto il codice del progetto
COPY . .

# 6️⃣ Espone la porta usata dal backend
EXPOSE 8080

# 7️⃣ Comando per avviare l'app senza reload (produzione H24)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
