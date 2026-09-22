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
echo -e "\n${YELLOW}[INFO] Puerto configurado: $PORT${NC}"

# ============================================================
# Paso 1: Instalar dependencias
# ============================================================
echo -e "\n${GREEN}[1/4] Instalando dependencias...${NC}"
pip install --upgrade pip
pip install -r requirements.txt
echo -e "${GREEN}[✓] Dependencias instaladas correctamente${NC}"

# ============================================================
# Paso 2: Ejecutar migraciones con Alembic
# ============================================================
echo -e "\n${GREEN}[2/4] Ejecutando migraciones de base de datos...${NC}"
alembic upgrade head
echo -e "${GREEN}[✓] Migraciones aplicadas correctamente${NC}"

# ============================================================
# Paso 3: Ejecutar seed de datos iniciales
# ============================================================
echo -e "\n${GREEN}[3/4] Ejecutando seed de datos iniciales...${NC}"
python seed.py
echo -e "${GREEN}[✓] Seed ejecutado correctamente${NC}"

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
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
