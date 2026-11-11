#!/bin/bash
# ============================================================
# Script de inicio para FashionStore Backend
# Instalación de dependencias, migraciones, seed y arranque
# ============================================================

set -e  # Detener si hay algún error

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  FashionStore Backend - Setup & Start  ${NC}"
echo -e "${BLUE}========================================${NC}"

# Detectar puerto (Render usa $PORT, local usa 8000)
PORT=${PORT:-8000}
# Numero de workers de gunicorn. En el plan free de Render el contenedor
# tiene 512 MB y 0.1 CPU: cada worker carga su propia copia del runtime
# (~200 MB medidos) y un worker es mas que suficiente para esa CPU. Con 2
# workers el contenedor se queda sin memoria, Render lo reinicia y el proxy
# responde 502 "Bad Gateway" (el navegador lo reporta como error de CORS).
# Se puede subir con la variable de entorno WEB_CONCURRENCY si se cambia de plan.
WORKERS=${WEB_CONCURRENCY:-1}
echo -e "\n${YELLOW}[INFO] Puerto configurado: $PORT${NC}"
echo -e "${YELLOW}[INFO] Workers de gunicorn: $WORKERS${NC}"

# ============================================================
# Paso 1: Instalar dependencias
# El build de Render ya ejecuta `pip install -r requirements.txt`, asi que
# aqui solo se reinstala si de verdad falta algo. Repetir el pip install en
# cada arranque (scikit-learn, pandas, numpy...) delayaba el arranque varios
# minutos y durante esa ventana el proxy de Render respondia 502.
# ============================================================
echo -e "\n${GREEN}[1/4] Verificando dependencias...${NC}"
if python -c "import fastapi, uvicorn, gunicorn, sqlalchemy, pandas, sklearn" >/dev/null 2>&1; then
  echo -e "${GREEN}[OK] Dependencias ya instaladas por el build step${NC}"
else
  echo -e "${YELLOW}[INFO] Faltan dependencias, instalando...${NC}"
  pip install --upgrade pip
  pip install -r requirements.txt
fi
echo -e "${GREEN}[✓] Dependencias instaladas correctamente${NC}"

# ============================================================
# Paso 2: Ejecutar migraciones con Alembic
# No es fatal a proposito: con `set -e` un fallo aqui impedia que gunicorn
# arrancara y el servicio se quedaba respondiendo 502 de forma permanente.
# Ademas el lifespan de la app tambien crea las tablas que falten
# (Base.metadata.create_all), asi que la API puede seguir operando.
# ============================================================
echo -e "\n${GREEN}[2/4] Ejecutando migraciones de base de datos...${NC}"
alembic upgrade head || echo -e "${YELLOW}[WARN] 'alembic upgrade head' fallo; se continua igual${NC}"

# ============================================================
# Paso 3: Ejecutar seed de datos iniciales
# El seed es idempotente, pero si falla no debe dejar el API caido: solo son
# datos de demostracion.
# ============================================================
echo -e "\n${GREEN}[3/4] Ejecutando seed de datos iniciales...${NC}"
python seed.py || echo -e "${YELLOW}[WARN] 'seed.py' fallo; se continua igual${NC}"

# ============================================================
# Paso 4: Iniciar la aplicación con Gunicorn
# ============================================================
echo -e "\n${GREEN}[4/4] Iniciando FashionStore API...${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  API disponible en: http://0.0.0.0:${PORT}${NC}"
echo -e "${BLUE}  Documentación:     http://0.0.0.0:${PORT}/docs${NC}"
echo -e "${BLUE}========================================${NC}\n"

exec gunicorn main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:$PORT" \
    --workers "$WORKERS" \
    --timeout 120 \
    --graceful-timeout 30 \
    --keepalive 5 \
    --access-logfile - \
    --error-logfile -
