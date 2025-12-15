# 1️⃣ Usa Python 3.11.9 come base
FROM python:3.11.9-slim

# 2️⃣ Imposta la working directory dentro il container
WORKDIR /code

# 3️⃣ Copia requirements.txt e installa dipendenze
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 4️⃣ Copia tutto il codice del progetto
COPY . .

# 5️⃣ Espone la porta usata dal backend
EXPOSE 8080

# 6️⃣ Comando per avviare l'app con uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
