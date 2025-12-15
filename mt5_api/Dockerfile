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

# 6️⃣ Comando per avviare lo script run_multi_instances.py
CMD ["python", "run_multi_instances.py"]
