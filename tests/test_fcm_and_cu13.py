import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_registro_dispositivo_fcm(client: AsyncClient):
    """Verifica que un cliente autenticado pueda registrar y desregistrar su token FCM."""
    # 1. Registrar un cliente
    nuevo_cliente = {
        "nombre": "Elena",
        "apellido": "Rios",
        "correo": "elena.fcm@test.com",
        "telefono": "78899001",
        "nit_ci": "8877665-FCM",
        "direccion_envio": "Calle 1, Zona Sur",
        "contrasena": "Cliente123!"
    }
    reg_resp = await client.post("/api/v1/auth/register", json=nuevo_cliente)
    assert reg_resp.status_code == 200
    token_cliente = reg_resp.json()["access_token"]

    # 2. Registrar token FCM
    payload = {
        "token": "fcm_test_token_sample_1234567890_abcdef",
        "tipo_dispositivo": "web"
    }
    response = await client.post(
        "/api/v1/notificaciones/dispositivos",
        json=payload,
        headers={"Authorization": f"Bearer {token_cliente}"}
    )
    assert response.status_code == 200, f"Error: {response.text}"
    data = response.json()
    assert data["token"] == payload["token"]
    assert data["activo"] is True

    # 3. Desregistrar token FCM
    del_resp = await client.delete(
        "/api/v1/notificaciones/dispositivos",
        params={"token": payload["token"]},
        headers={"Authorization": f"Bearer {token_cliente}"}
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True
