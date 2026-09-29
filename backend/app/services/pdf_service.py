import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings
from app.core.logging import logger


class PDFService:
    def __init__(self):
        self.output_dir = os.path.abspath(settings.PDF_STORAGE_DIR)
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_quotation_pdf(self, quote_data: Dict[str, Any]) -> str:
        """
        Generate a professional vehicle quotation PDF and return the absolute file path.
        """
        quotation_number = quote_data["quotation_number"]
        filename = f"{quotation_number}.pdf"
        filepath = os.path.join(self.output_dir, filename)

        doc = SimpleDocTemplate(
            filepath,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()
        
        # Custom Typography Styles
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#1A365D"),
            spaceAfter=4,
        )
        subtitle_style = ParagraphStyle(
            "DocSubtitle",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#4A5568"),
        )
        section_style = ParagraphStyle(
            "SectionTitle",
            parent=styles["Heading2"],
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#2B6CB0"),
            spaceBefore=10,
            spaceAfter=4,
        )
        cell_style = ParagraphStyle(
            "CellNormal",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#2D3748"),
        )
        cell_bold = ParagraphStyle(
            "CellBold",
            parent=styles["Normal"],
            fontSize=8.5,
            leading=11,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#1A202C"),
        )

        story = []

        # 1. Header Banner Table
        header_text = [
            Paragraph("<b>ANTIGRAVITY TWO-WHEELER SHOWROOM</b>", title_style),
            Paragraph("Official Authorized Dealership & Sales Automation Center", subtitle_style),
            Paragraph("100 Feet Ring Road, Tech Corridor | Contact: +91 98765 43210 | info@dealership.com", subtitle_style),
        ]
        quote_meta = [
            Paragraph(f"<b>QUOTATION NO:</b> {quotation_number}", cell_bold),
            Paragraph(f"<b>Date:</b> {quote_data.get('date', datetime.now().strftime('%d-%b-%Y'))}", cell_style),
            Paragraph(f"<b>Valid Until:</b> {quote_data.get('valid_until', '15 Days from Issue')}", cell_style),
            Paragraph(f"<b>Status:</b> {quote_data.get('status', 'GENERATED')}", cell_bold),
        ]

        header_table = Table([[header_text, quote_meta]], colWidths=[360, 180])
        header_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ])
        )
        story.append(header_table)
        story.append(Spacer(1, 8))

        # 2. Customer & Sales Rep Info
        cust = quote_data.get("customer", {})
        rep = quote_data.get("sales_executive", {})
        variant = quote_data.get("variant", {})

        cust_info = [
            Paragraph("<b>CUSTOMER DETAILS</b>", section_style),
            Paragraph(f"<b>Name:</b> {cust.get('full_name', 'N/A')}", cell_style),
            Paragraph(f"<b>Phone:</b> {cust.get('phone', 'N/A')}", cell_style),
            Paragraph(f"<b>Email:</b> {cust.get('email', 'N/A')}", cell_style),
            Paragraph(f"<b>City/Address:</b> {cust.get('city', '')} {cust.get('address', '')}", cell_style),
        ]

        rep_info = [
            Paragraph("<b>SALES EXECUTIVE</b>", section_style),
            Paragraph(f"<b>Name:</b> {rep.get('full_name', 'N/A')}", cell_style),
            Paragraph(f"<b>Email:</b> {rep.get('email', 'N/A')}", cell_style),
            Paragraph(f"<b>Phone:</b> {rep.get('phone', 'Showroom Desk')}", cell_style),
        ]

        info_table = Table([[cust_info, rep_info]], colWidths=[320, 220])
        info_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        story.append(info_table)
        story.append(Spacer(1, 8))

        # 3. Vehicle Specifications
        story.append(Paragraph("<b>SELECTED VEHICLE DETAILS</b>", section_style))
        veh_data = [
            [
                Paragraph(f"<b>Model:</b> {variant.get('name', 'N/A')}", cell_style),
                Paragraph(f"<b>Engine:</b> {variant.get('engine_cc', 'N/A')} CC", cell_style),
                Paragraph(f"<b>Fuel Type:</b> {variant.get('fuel_type', 'PETROL')}", cell_style),
                Paragraph(f"<b>Transmission:</b> {variant.get('transmission', 'AUTOMATIC')}", cell_style),
            ]
        ]
        veh_table = Table(veh_data, colWidths=[180, 120, 120, 120])
        veh_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF2F7")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ])
        )
        story.append(veh_table)
        story.append(Spacer(1, 8))

        # 4. Itemized On-Road Price Breakdown
        story.append(Paragraph("<b>PRICE BREAKDOWN (ON-ROAD)</b>", section_style))
        price_rows = [
            [Paragraph("<b>Component / Line Item</b>", cell_bold), Paragraph("<b>Details</b>", cell_bold), Paragraph("<b>Amount (INR)</b>", cell_bold)],
            [Paragraph("Ex-Showroom Price", cell_style), Paragraph("Base Vehicle Manufacturing Cost", cell_style), Paragraph(f"Rs. {quote_data.get('base_price', '0.00')}", cell_style)],
            [Paragraph("Comprehensive Insurance", cell_style), Paragraph("1-Year Own Damage + 5-Year Third Party", cell_style), Paragraph(f"Rs. {quote_data.get('insurance', '0.00')}", cell_style)],
            [Paragraph("RTO Registration & Taxes", cell_style), Paragraph("State Road Tax & Permanent Registration", cell_style), Paragraph(f"Rs. {quote_data.get('rto', '0.00')}", cell_style)],
        ]

        # Accessories list
        items: List[Dict[str, Any]] = quote_data.get("items", [])
        if items:
            for item in items:
                price_rows.append([
                    Paragraph(f"Accessory: {item.get('item_name')}", cell_style),
                    Paragraph(f"Qty: {item.get('quantity', 1)} @ Rs. {item.get('unit_price')}", cell_style),
                    Paragraph(f"Rs. {item.get('total_price')}", cell_style),
                ])
        else:
            price_rows.append([
                Paragraph("Accessories & Add-ons", cell_style),
                Paragraph("Standard Included", cell_style),
                Paragraph(f"Rs. {quote_data.get('accessories', '0.00')}", cell_style),
            ])

        # Discount
        discount_val = Decimal(str(quote_data.get("discount", "0.00")))
        if discount_val > Decimal("0"):
            price_rows.append([
                Paragraph(f"Special Offer / Discount ({quote_data.get('offer_title', 'Festive Offer')})", cell_bold),
                Paragraph("Applied Promotion Discount", cell_style),
                Paragraph(f"- Rs. {discount_val:.2f}", cell_bold),
            ])

        # Final Price Row
        price_rows.append([
            Paragraph("<b>TOTAL ON-ROAD FINAL PRICE</b>", cell_bold),
            Paragraph("All Inclusive", cell_bold),
            Paragraph(f"<b>Rs. {quote_data.get('final_price', '0.00')}</b>", cell_bold),
        ])

        price_table = Table(price_rows, colWidths=[200, 220, 120])
        price_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EBF8FF")),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ])
        )
        story.append(price_table)
        story.append(Spacer(1, 8))

        # 5. Finance & Loan Estimation Table
        down_payment = Decimal(str(quote_data.get("down_payment", "0.00")))
        loan_amount = Decimal(str(quote_data.get("loan_amount", "0.00")))

        if loan_amount > Decimal("0"):
            story.append(Paragraph("<b>FINANCE & EMI SCHEME</b>", section_style))
            finance_rows = [
                [
                    Paragraph("<b>Down Payment</b>", cell_style),
                    Paragraph(f"Rs. {down_payment:.2f}", cell_bold),
                    Paragraph("<b>Loan Principal</b>", cell_style),
                    Paragraph(f"Rs. {loan_amount:.2f}", cell_bold),
                ],
                [
                    Paragraph("<b>Tenure</b>", cell_style),
                    Paragraph(f"{quote_data.get('loan_tenure', 36)} Months", cell_style),
                    Paragraph("<b>Interest Rate</b>", cell_style),
                    Paragraph(f"{quote_data.get('interest_rate', '0.00')}% p.a.", cell_style),
                ],
                [
                    Paragraph("<b>Monthly EMI</b>", cell_bold),
                    Paragraph(f"<b>Rs. {quote_data.get('emi', '0.00')} / mo</b>", cell_bold),
                    Paragraph("<b>Financier</b>", cell_style),
                    Paragraph("In-house Partner Bank", cell_style),
                ],
            ]
            finance_table = Table(finance_rows, colWidths=[130, 140, 130, 140])
            finance_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ])
            )
            story.append(finance_table)
            story.append(Spacer(1, 8))

        # 6. Terms and Signatures
        terms_text = (
            "<b>Terms & Conditions:</b><br/>"
            "1. Quotation prices are valid for 15 days from the date of issuance.<br/>"
            "2. Final price at vehicle delivery is subject to statutory RTO and insurance revisions, if any.<br/>"
            "3. Booking is confirmed only upon receipt of booking deposit and chassis allocation."
        )
        story.append(Paragraph(terms_text, subtitle_style))
        story.append(Spacer(1, 15))

        sig_table = Table(
            [[Paragraph("Customer Acceptance Signature: __________________", cell_style), Paragraph("Authorized Showroom Signature: __________________", cell_style)]],
            colWidths=[270, 270],
        )
        story.append(sig_table)

        doc.build(story)
        logger.info(f"Generated quotation PDF: {filepath}")
        return filepath
