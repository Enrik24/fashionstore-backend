"""
Pruebas para Reportes de Compras y Reservas en el Asistente Virtual para Clientes (CU23).
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_cliente_reportes_endpoints(client: AsyncClient):
    # 1. Registrar e iniciar sesión como cliente
    reg_resp = await client.post("/api/v1/auth/register", json={
        "nombre": "Ana",
        "apellido": "Cliente",
        "correo": "ana.cliente.reporte@fashionstore.com",
        "telefono": "79998877",
        "nit_ci": "9876543",
        "contrasena": "Cliente123!"
    })
    assert reg_resp.status_code == 200
    token = reg_resp.json()["access_token"]
    client_headers = {"Authorization": f"Bearer {token}"}

    # 2. Consultar resumen de reportes del cliente
    res_summary = await client.get("/api/v1/cliente/reportes/resumen", headers=client_headers)
    assert res_summary.status_code == 200
    data_sum = res_summary.json()
    assert "compras" in data_sum
    assert "reservas" in data_sum

    # 3. Exportar reporte de compras en JSON, PDF y Excel
    res_compras_json = await client.get("/api/v1/cliente/reportes/compras/export?formato=JSON", headers=client_headers)
    assert res_compras_json.status_code == 200
    assert "total_compras" in res_compras_json.json()

    res_compras_pdf = await client.get("/api/v1/cliente/reportes/compras/export?formato=PDF", headers=client_headers)
    assert res_compras_pdf.status_code == 200
    assert res_compras_pdf.headers["content-type"] == "application/pdf"

    res_compras_excel = await client.get("/api/v1/cliente/reportes/compras/export?formato=EXCEL", headers=client_headers)
    assert res_compras_excel.status_code == 200
    assert "spreadsheetml" in res_compras_excel.headers["content-type"]

    # 4. Exportar reporte de reservas en JSON, PDF y Excel
    res_reservas_json = await client.get("/api/v1/cliente/reportes/reservas/export?formato=JSON", headers=client_headers)
    assert res_reservas_json.status_code == 200
    assert "total_reservas" in res_reservas_json.json()

    res_reservas_pdf = await client.get("/api/v1/cliente/reportes/reservas/export?formato=PDF", headers=client_headers)
    assert res_reservas_pdf.status_code == 200
    assert res_reservas_pdf.headers["content-type"] == "application/pdf"

    # 5. Probar el chat del Asistente Virtual solicitando reporte de compras
    chat_resp = await client.post("/api/v1/inteligencia/asistente-chat", headers=client_headers, json={
        "mensaje": "Genera un reporte de mis compras en pdf",
        "historial": []
    })
    assert chat_resp.status_code == 200
    chat_data = chat_resp.json()
    assert "respuesta" in chat_data
    assert chat_data["accion"] == "reporte_compras"
    assert chat_data["tipo_respuesta"] == "reporte"
    assert chat_data["datos_reporte"] is not None
    assert chat_data["datos_reporte"]["tipo"] == "compras"

    # 6. Probar el chat del Asistente Virtual solicitando reporte de reservas
    chat_resp2 = await client.post("/api/v1/inteligencia/asistente-chat", headers=client_headers, json={
        "mensaje": "Quiero descargar un reporte de mis reservas",
        "historial": []
    })
    assert chat_resp2.status_code == 200
    chat_data2 = chat_resp2.json()
    assert "respuesta" in chat_data2
    assert chat_data2["accion"] == "reporte_reservas"
    assert chat_data2["tipo_respuesta"] == "reporte"
    assert chat_data2["datos_reporte"] is not None
    assert chat_data2["datos_reporte"]["tipo"] == "reservas"
