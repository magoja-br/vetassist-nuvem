# Imagem de produção do VetAssist AI (versão nuvem).
FROM python:3.11-slim

WORKDIR /app

# Instala as dependências primeiro (aproveita o cache de camadas).
COPY BACKEND/requirements.txt BACKEND/requirements.txt
RUN pip install --no-cache-dir -r BACKEND/requirements.txt

# Copia o código e as telas.
COPY BACKEND/ BACKEND/
COPY frontend/ frontend/

# O backend importa "app.main", então rodamos de dentro de BACKEND.
WORKDIR /app/BACKEND

# A porta real vem da variável PORT (o provedor define). 8000 é o padrão.
ENV PORT=8000
EXPOSE 8000

CMD ["python", "run.py"]
