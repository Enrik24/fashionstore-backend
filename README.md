# FashionStore Backend

Backend de la plataforma de comercio electrónico FashionStore desarrollado con FastAPI, SQLAlchemy y PostgreSQL.

## Requisitos Previos

- Python 3.10+
- PostgreSQL 14+
- pip (gestor de paquetes)

## Instalación

1. **Clonar o acceder al directorio del proyecto:**
   ```bash
   cd fashionstore-backend
   ```

2. **Crear y activar el entorno virtual:**
   ```bash
   # Windows
   python -m venv venv
   venv\Scripts\activate

   # Linux/Mac
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Instalar las dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar las variables de entorno:**

   El archivo `.env` ya está configurado con los valores por defecto. Asegúrate de:
   - Verificar que PostgreSQL esté corriendo
   - Crear la base de datos `tiendaRopa` si no existe:
     ```sql
     CREATE DATABASE tiendaRopa;
     ```

5. **Ejecutar el servidor:**
   ```bash
   # Con recarga automática (desarrollo)
   uvicorn main:app --reload --host 0.0.0.0 --port 8000

   # O simplemente
   python main.py
   ```

6. **Acceder a la documentación:**
   - Swagger UI: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc

## Credenciales por Defecto

El sistema crea un usuario administrador por defecto al iniciar:
- **Correo:** admin@fashionstore.com
- **Contraseña:** Admin123!

## Estructura del Proyecto

```
fashionstore-backend/
├── main.py                    # Punto de entrada de la aplicación
├── requirements.txt           # Dependencias del proyecto
├── .env                       # Variables de entorno
├── .gitignore                 # Archivos ignorados por git
├── alembic.ini               # Configuración de Alembic
│
├── app/
│   ├── __init__.py
│   ├── config.py             # Configuración global
│   ├── database.py           # Conexión a PostgreSQL
│   ├── security.py           # Funciones de seguridad (JWT, hashing)
│   ├── exceptions.py         # Excepciones personalizadas
│   │
│   ├── apps/                 # "Apps" del sistema
│   │   ├── gestion_usuarios/    # App 1: Usuarios y Autenticación
│   │   ├── gestion_catalogo/    # App 2: Catálogo e Inventario
│   │   ├── gestion_ventas/      # App 3: Ventas, Reservas y Pagos
│   │   └── servicios_inteligentes/  # App 4: IA y Reportes
│   │
│   └── services/             # Servicios externos
│       └── cloudinary_service.py
│
├── alembic/                  # Migraciones de base de datos
│   ├── env.py
│   └── versions/
│
└── tests/                    # Tests unitarios
```

## Iteración #1 - Funcionalidades Implementadas

### App 1: Gestión de Usuarios y Autenticación
- ✅ CU01 - Gestión de autenticación (login, logout, refresh token)
- ✅ CU02 - Registro de clientes
- ✅ CU03 - Gestionar usuarios (admin)
- ✅ CU04 - Gestionar roles y permisos
- ✅ CU22 - Bitácora del sistema
- ✅ CU23 - Gestionar perfil del cliente

### App 2: Gestión de Catálogo, Productos e Inventario
- ✅ CU05 - Gestión de sucursales y ciudades
- ✅ CU06 - Gestión de productos
- ✅ CU08 - Gestión de proveedores
- ✅ CU19 - Gestionar inventario

## Casos de Uso de la Iteración 2 (Próximamente)
- CU09 - Explorar catálogo
- CU10 - Consultar disponibilidad por sucursal
- CU11 - Gestionar carrito de compras
- CU12 - Realizar reserva de prendas
- CU14 - Atender reservas (sucursal)
- CU16 - Realizar compra digital
- CU17 - Registrar venta presencial
- CU18 - Gestionar pagos

## Comandos Útiles

```bash
# Ejecutar con hot-reload
uvicorn main:app --reload

# Ejecutar tests
pytest tests/ -v

# Crear una migración
alembic revision --autogenerate -m "mensaje de la migración"

# Aplicar migraciones
alembic upgrade head

# Revertir última migración
alembic downgrade -1
```

## Integraciones

### Cloudinary
El servicio de imágenes está configurado. Para usar:
```python
from app.services.cloudinary_service import CloudinaryService

# Subir imagen
result = CloudinaryService.upload_image(file, folder="productos")
```

### Stripe (Pendiente)
Las credenciales de Stripe están预留 en `.env`. Cuando estén disponibles:
```env
STRIPE_API_KEY=sk_test_xxxxx
STRIPE_PUBLISHABLE_KEY=pk_test_xxxxx
```

### Groq API (Pendiente de configurar en Iteración 3)
```env
GROQ_API_KEY=tu_api_key
```

## Tecnologías Utilizadas

- **FastAPI** - Framework web moderno y rápido
- **SQLAlchemy** - ORM para base de datos
- **PostgreSQL** - Base de datos relacional
- **Pydantic** - Validación de datos
- **python-jose** - Manejo de JWT
- **Passlib** - Hashing de contraseñas
- **Alembic** - Migraciones de base de datos

## Notas de Desarrollo

- El proyecto usa SQLAlchemy async para mejor rendimiento
- Las APIs están versionadas en `/api/v1/`
- La autenticación usa JWT con access y refresh tokens
- Los roles disponibles son: Administrador, Cliente, Encargado, Cajero