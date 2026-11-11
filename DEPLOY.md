# Guía de despliegue (Render + Vercel)

Este documento explica por qué el dashboard desplegado devolvía errores de
CORS y qué hay que configurar en cada plataforma para que no vuelva a pasar.

---

## 1. El error que aparecía no era de CORS

En la consola del navegador se veía:

```
Access to XMLHttpRequest at 'https://fashionstore-backend-3wsr.onrender.com/...'
has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present
GET ... net::ERR_FAILED 502 (Bad Gateway)
```

El **502 Bad Gateway** es lo importante. Cuando el proxy de Render no puede
alcanzar el contenedor (porque está reiniciándose, se quedó sin memoria o se
suspendió), devuelve su propia página de error, que **no trae cabeceras CORS**.
El navegador no puede leer esa respuesta y por eso la reporta como un bloqueo
de CORS.

Comprobación: con `Origin: https://fashionstore-frontend-smoky.vercel.app` el
backend devuelve correctamente la cabecera `access-control-allow-origin`. La
configuración de CORS siempre estuvo bien.

---

## 2. Por qué el backend devolvía 502

### 2.1 El contenedor se quedaba sin memoria (causa principal)

El plan free de Render da **512 MB de RAM**. Cada worker de gunicorn carga su
propia copia del runtime, y se midió en este proyecto:

| Estado del worker                     | Memoria (working set) |
| ------------------------------------- | --------------------- |
| Antes (numpy/pandas/sklearn al inicio) | **244 MB**           |
| Ahora (imports perezosos)              | **196 MB**           |

Con `--workers 2` eran ~489 MB sobre 512 MB: sin margen para el sistema ni para
el pico de un reporte. El contenedor moría por OOM, Render lo reiniciaba y
durante el reinicio el proxy respondía 502.

**Cambios aplicados:**

- `start.sh`: `--workers "$WORKERS"` con `WEB_CONCURRENCY` (por defecto **1**).
- `app/apps/analitica_avanzada/ml_service.py`: `numpy`, `pandas` y `joblib` se
  importan de forma **perezosa** (solo cuando un endpoint de ML los usa).
- `app/services/firebase_service.py`: `firebase_admin` (~14 MB) también se
  importa de forma perezosa.

### 2.2 El arranque tardaba minutos

`start.sh` repetía `pip install -r requirements.txt` (scikit-learn, pandas,
numpy...) en **cada** arranque, y solo después corría las migraciones, el seed
y por último gunicorn. Con `set -e`, cualquier fallo en migraciones o seed
impedía que gunicorn arrancara y el servicio se quedaba **502 de forma
permanente**.

**Cambios aplicados:**

- El `pip install` solo corre si faltan dependencias (el build de Render ya las
  instala).
- `alembic upgrade head` y `python seed.py` ya **no son fatales**: avisan con un
  `[WARN]` y el API arranca igual.

### 2.3 El servicio free se suspendía por inactividad

Render duerme el servicio free tras ~15 min sin tráfico. La primera petición
mientras despierta recibe un 502. El keep-alive que ya existe en `main.py`
necesita la variable `RENDER_PUBLIC_URL`; si está vacía, **no corre**.

---

## 3. Qué configurar en el dashboard de Render

El servicio se creó a mano en el dashboard (no con el blueprint), así que
`render.yaml` no se aplicó solo. Hay que fijar estas variables de entorno en
**Render → tu servicio → Environment**:

| Variable               | Valor                                                                 |
| ---------------------- | --------------------------------------------------------------------- |
| `CORS_ORIGINS`         | `https://fashionstore-frontend-smoky.vercel.app,http://localhost:4200,http://localhost:3000` |
| `CORS_ORIGINS_REGEX`   | `https://fashionstore-frontend(-[a-z0-9-]+)?\.vercel\.app`            |
| `RENDER_PUBLIC_URL`    | `https://fashionstore-backend-3wsr.onrender.com`                       |
| `KEEPALIVE_INTERVALO_SEG` | `600`                                                               |
| `WEB_CONCURRENCY`      | `1`                                                                    |

- `CORS_ORIGINS_REGEX` permite las **URLs de preview de Vercel**, que cambian
  de subdominio en cada push (`fashionstore-frontend-<hash>.vercel.app`). Sin
  ella, cada preview nuevo recibe el mismo error de CORS.
- `RENDER_PUBLIC_URL` activa el keep-alive anti-suspensión.
- `WEB_CONCURRENCY=1` evita el OOM. Subirlo solo si el plan sube de memoria.

Después: **Manual Deploy → Deploy latest commit** en el backend.

> En Render, `Start Command` debe ser `chmod +x start.sh && ./start.sh`
> y `Health Check Path` `/health`.

---

## 4. Qué configurar en Vercel

- **Root Directory**: `fashionstore-frontend`
- **Output Directory**: `dist/fashionstore-frontend/browser` (builder nuevo de
  Angular 17; la carpeta `browser` es obligatoria)
- **Framework preset**: Angular
- La API ya va por URL absoluta (`environment.prod.ts`), no hace falta rewrite
  de `/api` a Vercel.

El routing de la SPA ya funciona (las rutas profundas como `/admin/products`
devuelven 200).

---

## 5. Cambios en el frontend (resiliencia)

`src/app/core/interceptors/retry.interceptor.ts` (nuevo):

- Reintenta con backoff exponencial (1.2 s, 2.4 s, 4.8 s) las peticiones
  **idempotentes** (GET/HEAD/OPTIONS) que fallan con estado `0`, `502`, `503` o
  `504`. El estado `0` es lo que Angular ve cuando el navegador bloquea la
  respuesta por CORS, así que cubre exactamente el fallo reportado.
- Los POST no se reintentan (un reintento podría duplicar un cobro).
- Va registrado como el interceptor **más interno**, de modo que el aviso
  (`errorInterceptor`) se muestra una sola vez, después de agotar los reintentos.

`src/app/core/interceptors/error.interceptor.ts`: ahora un `502/503/504`
muestra *"El servidor se está reiniciando o no responde"* en lugar del mensaje
genérico.

El WebSocket de pagos ya tenía reconexión con backoff
(`stock-websocket.service.ts`), por lo que se recupera solo; el fallo que se
vio era consecuencia del backend caído.

---

## 6. Verificar

```bash
# debe responder 200 con la cabecera CORS
curl -i -H "Origin: https://fashionstore-frontend-smoky.vercel.app" \
  https://fashionstore-backend-3wsr.onrender.com/health

# con token válido
curl -i -H "Authorization: Bearer <token>" \
  -H "Origin: https://fashionstore-frontend-smoky.vercel.app" \
  https://fashionstore-backend-3wsr.onrender.com/api/v1/kpis/dashboard
```

En el panel de Render, la pestaña **Metrics** debe mostrar memoria por debajo
de ~250 MB y sin reinicios del contenedor.

---

## 7. Cambiar de proveedor de base de datos (Render → Railway)

El motor es SQLAlchemy **async** con el driver `asyncpg`, que usa el
parámetro `ssl` (no `sslmode`). Por eso **no hay que escribir
`?sslmode=require` en la URL**: SQLAlchemy le pasa a asyncpg cualquier
parámetro de la query tal cual y la conexión falla con

```
TypeError: connect() got an unexpected keyword argument 'sslmode'
```

### Opción A: variables sueltas (recomendada)

```env
DB_NAME=railway
DB_USER=postgres
DB_PASSWORD=<password>
DB_HOST=trolley.proxy.rlwy.net
DB_PORT=13387
DB_SSLMODE=require
```

`app/config.py` arma la URL (`postgresql+asyncpg://...`) y escapa usuario y
contraseña con `quote_plus`. `DB_SSLMODE` es un modo estilo libpq:

| Modo         | Significado                                        |
| ------------ | -------------------------------------------------- |
| `disable`    | Sin TLS                                            |
| `allow`      | TLS solo si el servidor lo exige                   |
| `prefer`     | TLS si el servidor lo ofrece (default de asyncpg)  |
| `require`    | TLS obligatorio, sin verificar el certificado       |
| `verify-ca`  | TLS verificando la CA (necesita `DB_SSLROOTCERT`)   |
| `verify-full`| TLS verificando CA **y** nombre del host            |

`require` es el equivalente del `sslmode=require` de libpq que trae la URL de
Railway: cifra el canal sin validar el certificado del servidor.

### Opción B: `DATABASE_URL` completa

```env
DATABASE_URL=postgresql://postgres:<password>@trolley.proxy.rlwy.net:13387/railway
DB_SSLMODE=require
```

`DATABASE_URL` tiene prioridad sobre `DB_*`. Importante: **sin** el `?sslmode=...`
en la URL. El modo SSL se sigue aplicando con `DB_SSLMODE`.

### Railway: verificación real del certificado (opcional)

Railway entrega el CA como `root.crt`. Para usarlo:

```env
DB_SSLMODE=verify-full
DB_SSLROOTCERT=/ruta/al/root.crt
```

Sin `DB_SSLROOTCERT`, el arranque falla con un error explícito.

### Checks

```bash
# desde la carpeta del backend, con el venv
python -c "from app.config import settings; print(settings.DATABASE_URL, settings.db_sslmode)"

# confirmar que el canal está cifrado
python -c "
import asyncio; from app.database import engine; from sqlalchemy import text
async def m():
    async with engine.connect() as c:
        print((await c.execute(text('select ssl from pg_stat_ssl where pid=pg_backend_pid()'))).scalar())
asyncio.run(m())"
```

`True` = la conexión va cifrada.

> Railway entrega una base **PostgreSQL 18**. El esquema del proyecto se crea
> solo: `alembic upgrade head` y, en el lifespan de la app,
> `Base.metadata.create_all`. La base de Railway recién creada está vacía, así
> que hay que correr el seed (`python seed.py`) para tener los datos de
> demostración.

