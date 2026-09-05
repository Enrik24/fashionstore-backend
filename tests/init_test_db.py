import asyncio
import asyncpg
from app.config import settings

async def create_test_db():
    user = settings.DB_USER
    password = settings.DB_PASSWORD
    host = settings.DB_HOST
    port = settings.DB_PORT
    test_db_name = f"{settings.DB_NAME}_test"
    
    conn = await asyncpg.connect(
        user=user,
        password=password,
        host=host,
        port=port,
        database="postgres"
    )
    
    exists = await conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = $1", test_db_name
    )
    if not exists:
        await conn.execute(f'CREATE DATABASE "{test_db_name}"')
        print(f"Base de datos {test_db_name} creada con éxito.")
    else:
        print(f"Base de datos {test_db_name} ya existe.")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(create_test_db())
