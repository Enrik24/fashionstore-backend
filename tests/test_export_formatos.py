"""Tests unitarios de exportación multiformato (CU20/CU15). Sin DB."""
from app.apps.servicios_inteligentes.export_service import (
    exportar_excel, exportar_html, exportar_pdf, responder_reporte,
)

DATOS = {
    "resumen": {"total_recaudado": 1500.5, "cantidad_pedidos_online": 10},
    "top_productos": [
        {"producto_id": 1, "nombre": "Polera", "unidades_vendidas": 5},
        {"producto_id": 2, "nombre": "Jean", "unidades_vendidas": 3},
    ],
}


def test_excel_magic_bytes():
    data = exportar_excel("VENTAS", DATOS)
    assert data[:2] == b"PK"


def test_html_standalone():
    html = exportar_html("VENTAS", DATOS)
    assert "<html" in html.lower() and "Polera" in html


def test_pdf_magic_bytes():
    data = exportar_pdf("VENTAS", DATOS)
    assert data[:4] == b"%PDF"


def test_dispatcher_nombres_y_media():
    content, media, fname = responder_reporte("VENTAS", DATOS, "EXCEL")
    assert media.startswith("application/vnd.openxmlformats")
    assert fname.endswith(".xlsx")
    content, media, fname = responder_reporte("VENTAS", DATOS, "PDF")
    assert media == "application/pdf" and fname.endswith(".pdf")
    content, media, fname = responder_reporte("VENTAS", DATOS, "HTML")
    assert media.startswith("text/html") and fname.endswith(".html")
