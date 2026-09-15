"""
Servicio de Generación de Comprobantes en formato PDF (Facturas y Tickets de Venta).
Utiliza ReportLab para generar documentos PDF de alta calidad para impresión o descarga.
"""
import io
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any

from reportlab.lib.pagesizes import letter, portrait
from reportlab.lib import colors
from reportlab.lib.units import inch, cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT


class ComprobantePDFService:
    """Generador de PDF para comprobantes y facturas de venta."""

    @staticmethod
    def generar_pdf_comprobante(
        comprobante_numero: str,
        tipo_comprobante: str,
        fecha: datetime,
        orden_numero: str,
        cliente_nombre: str,
        cliente_nit_ci: str,
        sucursal_nombre: str,
        sucursal_direccion: str,
        sucursal_telefono: str,
        cajero_nombre: Optional[str],
        metodo_pago: str,
        items: List[Dict[str, Any]],
        subtotal: Decimal,
        descuento: Decimal,
        total: Decimal
    ) -> bytes:
        """
        Genera un archivo PDF con el comprobante / factura de venta.
        
        Returns:
            bytes: Contenido binario del PDF generado.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        elements = []
        styles = getSampleStyleSheet()

        # Estilos personalizados
        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontName='Helvetica-Bold',
            fontSize=18,
            leading=22,
            textColor=colors.HexColor('#0F172A'),
            alignment=TA_CENTER
        )

        subtitle_style = ParagraphStyle(
            'SubtitleStyle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=10,
            leading=14,
            textColor=colors.HexColor('#475569'),
            alignment=TA_CENTER
        )

        section_title = ParagraphStyle(
            'SectionTitle',
            parent=styles['Heading3'],
            fontName='Helvetica-Bold',
            fontSize=11,
            leading=15,
            textColor=colors.HexColor('#E11D48'),
            alignment=TA_LEFT
        )

        normal_style = ParagraphStyle(
            'NormalStyle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=13,
            textColor=colors.HexColor('#1E293B')
        )

        bold_style = ParagraphStyle(
            'BoldStyle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=13,
            textColor=colors.HexColor('#0F172A')
        )

        right_bold = ParagraphStyle(
            'RightBold',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10,
            leading=14,
            textColor=colors.HexColor('#0F172A'),
            alignment=TA_RIGHT
        )

        total_accent = ParagraphStyle(
            'TotalAccent',
            parent=styles['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=14,
            leading=18,
            textColor=colors.HexColor('#E11D48'),
            alignment=TA_RIGHT
        )

        # 1. Cabecera de Empresa
        elements.append(Paragraph("FASHIONSTORE", title_style))
        elements.append(Paragraph("SMART FASHION & APPAREL", subtitle_style))
        elements.append(Paragraph(f"NIT: 1028475029 &bull; Sucursal: {sucursal_nombre}", subtitle_style))
        elements.append(Paragraph(f"{sucursal_direccion} &bull; Tel: {sucursal_telefono}", subtitle_style))
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#E11D48'), spaceAfter=15))

        # 2. Información del Comprobante
        tipo_lbl = "FACTURA DE VENTA" if tipo_comprobante == "FACTURA" else "RECIBO / TICKET DE VENTA"
        fecha_str = fecha.strftime('%d/%m/%Y %H:%M:%S') if isinstance(fecha, datetime) else str(fecha)
        
        info_data = [
            [
                Paragraph(f"<b>N° Comprobante:</b> {comprobante_numero}", normal_style),
                Paragraph(f"<b>Tipo:</b> {tipo_lbl}", normal_style)
            ],
            [
                Paragraph(f"<b>N° Orden:</b> {orden_numero}", normal_style),
                Paragraph(f"<b>Fecha de Emisión:</b> {fecha_str}", normal_style)
            ],
            [
                Paragraph(f"<b>Cliente:</b> {cliente_nombre}", normal_style),
                Paragraph(f"<b>NIT / CI:</b> {cliente_nit_ci or '0 (Sin NIT/CI)'}", normal_style)
            ],
            [
                Paragraph(f"<b>Método de Pago:</b> {metodo_pago}", normal_style),
                Paragraph(f"<b>Cajero / Atendido por:</b> {cajero_nombre or 'Terminal POS'}", normal_style)
            ]
        ]
        
        info_table = Table(info_data, colWidths=[270, 270])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#E2E8F0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#F1F5F9')),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 15))

        # 3. Tabla de Productos
        elements.append(Paragraph("DETALLE DE PRENDAS", section_title))
        elements.append(Spacer(1, 6))

        table_data = [
            [
                Paragraph("<b>Cant.</b>", bold_style),
                Paragraph("<b>SKU</b>", bold_style),
                Paragraph("<b>Descripción / Prenda</b>", bold_style),
                Paragraph("<b>Talla / Color</b>", bold_style),
                Paragraph("<b>P. Unit (Bs.)</b>", right_bold),
                Paragraph("<b>Subtotal (Bs.)</b>", right_bold)
            ]
        ]

        for it in items:
            table_data.append([
                Paragraph(str(it.get("cantidad", 1)), normal_style),
                Paragraph(str(it.get("sku", "")), normal_style),
                Paragraph(str(it.get("nombre", "")), normal_style),
                Paragraph(f"{it.get('talla', '-')} / {it.get('color', '-')}", normal_style),
                Paragraph(f"{Decimal(str(it.get('precio_unitario', 0))):.2f}", normal_style),
                Paragraph(f"{Decimal(str(it.get('subtotal', 0))):.2f}", normal_style)
            ])

        prod_table = Table(table_data, colWidths=[35, 75, 185, 95, 75, 75])
        prod_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#0F172A')),
            ('ALIGN', (4, 1), (-1, -1), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#CBD5E1')),
            ('LINEBELOW', (0, 1), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ]))
        elements.append(prod_table)
        elements.append(Spacer(1, 12))

        # 4. Resumen de Totales
        totals_data = [
            [Paragraph("Subtotal:", right_bold), Paragraph(f"Bs. {Decimal(str(subtotal)):.2f}", right_bold)],
            [Paragraph("Descuento:", right_bold), Paragraph(f"Bs. {Decimal(str(descuento)):.2f}", right_bold)],
            [Paragraph("TOTAL A PAGAR:", total_accent), Paragraph(f"Bs. {Decimal(str(total)):.2f}", total_accent)]
        ]
        totals_table = Table(totals_data, colWidths=[440, 100])
        totals_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(totals_table)
        elements.append(Spacer(1, 20))

        # 5. Pie de Página Legal
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#CBD5E1'), spaceAfter=10))
        legal_style = ParagraphStyle(
            'LegalStyle',
            parent=styles['Normal'],
            fontName='Helvetica-Oblique',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#64748B'),
            alignment=TA_CENTER
        )
        elements.append(Paragraph("ESTE DOCUMENTO ES UNA REPRESENTACIÓN IMPRESA DE UN COMPROBANTE DE VENTA ELECTRÓNICO.", legal_style))
        elements.append(Paragraph("¡Gracias por su compra en FashionStore! Conserve este comprobante para cualquier cambio o devolución.", legal_style))

        # Construir PDF
        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
