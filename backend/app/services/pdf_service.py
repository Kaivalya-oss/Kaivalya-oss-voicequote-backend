import os
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import BinaryIO, Optional, Any, Dict, List, Optional
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings
from app.core.logging import logger


class PDFService:
    def __init__(self):
        self.output_dir = os.path.abspath(settings.PDF_STORAGE_DIR)
        if not settings.MULTI_TENANT_ENABLED:
            os.makedirs(self.output_dir, exist_ok=True)

    def render_quotation_pdf(self, quote_data: Dict[str, Any]) -> bytes:
        """Build the PDF in memory, without writing customer details to disk."""
        buffer = BytesIO()
        self.generate_quotation_pdf(quote_data, target=buffer)
        return buffer.getvalue()

    def generate_quotation_pdf(self, quote_data: Dict[str, Any], target: Optional[BinaryIO] = None) -> str:
        """
        Generate a professional vehicle quotation PDF and return the absolute file path,
        or write it to `target` when one is given.
        Supports both single-vehicle and multiple vehicle comparison quotations.
        """
        quotation_number = quote_data["quotation_number"]
        filename = f"{quotation_number}.pdf"
        filepath = target if target is not None else os.path.join(self.output_dir, filename)

        doc = SimpleDocTemplate(
            filepath,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()

        if quote_data.get("is_comparison") and len(quote_data.get("options", [])) >= 2:
            story = self._build_comparison_quotation_story(quote_data, styles)
            doc.build(story)
            logger.info(f"Generated comparison quotation PDF: {filepath}")
            return filepath
        
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
            Paragraph("<b>NAVKAR TWO-WHEELER SHOWROOM</b>", title_style),
            Paragraph("Official Authorized Dealership & Sales Automation Center", subtitle_style),
            Paragraph("100 Feet Ring Road, Tech Corridor | Contact: +91 98765 43210 | info@dealership.com", subtitle_style),
        ]
        quote_meta = [
            Paragraph(f"<b>QUOTATION NO:</b> {quotation_number}", cell_bold),
            Paragraph(f"<b>Date:</b> {quote_data.get('date', datetime.now().strftime('%d-%b-%Y'))}", cell_style),
            Paragraph(f"<b>Valid Until:</b> {quote_data.get('valid_until', '15 Days from Issue')}", cell_style),
            Paragraph(f"<b>Status:</b> {quote_data.get('status', 'GENERATED')}", cell_bold),
            Paragraph(f"<b>Payment Mode:</b> {quote_data.get('payment_mode', 'CASH').title()}", cell_bold),
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

        # 5. Terms and Signatures
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

    def _build_comparison_quotation_story(self, quote_data: Dict[str, Any], styles) -> List[Any]:
        title_style = ParagraphStyle(
            "DocTitleCmp", parent=styles["Heading1"], fontSize=18, leading=22, textColor=colors.HexColor("#1A365D"), spaceAfter=4
        )
        subtitle_style = ParagraphStyle(
            "DocSubtitleCmp", parent=styles["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#4A5568")
        )
        section_style = ParagraphStyle(
            "SectionTitleCmp", parent=styles["Heading2"], fontSize=11, leading=14, textColor=colors.HexColor("#2B6CB0"), spaceBefore=8, spaceAfter=4
        )
        cell_style = ParagraphStyle(
            "CellNormalCmp", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#2D3748")
        )
        cell_bold = ParagraphStyle(
            "CellBoldCmp", parent=styles["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold", textColor=colors.HexColor("#1A202C")
        )
        cell_head = ParagraphStyle(
            "CellHeadCmp", parent=cell_bold, textColor=colors.whitesmoke
        )

        story = []
        quotation_number = quote_data["quotation_number"]
        cust = quote_data.get("customer", {})
        rep = quote_data.get("sales_executive", {})
        options = quote_data.get("options", [])
        opt_a = options[0]
        opt_b = options[1]

        # 1. Header Banner Table
        header_text = [
            Paragraph("<b>NAVKAR TWO-WHEELER SHOWROOM</b>", title_style),
            Paragraph("Official Authorized Dealership — Vehicle Comparison Quotation", subtitle_style),
            Paragraph("100 Feet Ring Road, Tech Corridor | Contact: +91 98765 43210 | info@dealership.com", subtitle_style),
        ]
        quote_meta = [
            Paragraph(f"<b>QUOTATION NO:</b> {quotation_number}", cell_bold),
            Paragraph(f"<b>Date:</b> {quote_data.get('date', datetime.now().strftime('%d-%b-%Y'))}", cell_style),
            Paragraph(f"<b>Valid Until:</b> {quote_data.get('valid_until', '15 Days from Issue')}", cell_style),
            Paragraph(f"<b>Status:</b> {quote_data.get('status', 'GENERATED')}", cell_bold),
            Paragraph(f"<b>Payment Mode:</b> {quote_data.get('payment_mode', 'CASH').title()}", cell_bold),
        ]

        header_table = Table([[header_text, quote_meta]], colWidths=[360, 180])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 6))

        # 2. Customer & Sales Rep Info
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
        info_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 8))

        # 3. Side-by-Side Comparison Table
        story.append(Paragraph("<b>VEHICLE COMPARISON OPTIONS</b>", section_style))

        def money(val):
            return f"Rs. {Decimal(str(val or 0)):,.2f}"

        cmp_rows = [
            [
                Paragraph("<b>Component / Spec</b>", cell_head),
                Paragraph(f"<b>OPTION A</b><br/>{opt_a.get('full_name', 'Vehicle A')}", cell_head),
                Paragraph(f"<b>OPTION B</b><br/>{opt_b.get('full_name', 'Vehicle B')}", cell_head),
            ],
            [
                Paragraph("Engine & Specs", cell_style),
                Paragraph(f"{opt_a.get('engine_cc', '-')} CC | {opt_a.get('fuel_type', '')} | {opt_a.get('transmission', '')}", cell_style),
                Paragraph(f"{opt_b.get('engine_cc', '-')} CC | {opt_b.get('fuel_type', '')} | {opt_b.get('transmission', '')}", cell_style),
            ],
            [
                Paragraph("Ex-Showroom Price", cell_style),
                Paragraph(money(opt_a.get('base_price')), cell_style),
                Paragraph(money(opt_b.get('base_price')), cell_style),
            ],
            [
                Paragraph("Comprehensive Insurance", cell_style),
                Paragraph(money(opt_a.get('insurance')), cell_style),
                Paragraph(money(opt_b.get('insurance')), cell_style),
            ],
            [
                Paragraph("RTO Registration & Tax", cell_style),
                Paragraph(money(opt_a.get('rto')), cell_style),
                Paragraph(money(opt_b.get('rto')), cell_style),
            ],
            [
                Paragraph("Accessories & Items", cell_style),
                Paragraph(money(opt_a.get('accessories')), cell_style),
                Paragraph(money(opt_b.get('accessories')), cell_style),
            ],
            [
                Paragraph("Special Offer / Discount", cell_style),
                Paragraph(f"- {money(opt_a.get('discount'))}" if Decimal(str(opt_a.get('discount', 0))) > 0 else "Rs. 0.00", cell_style),
                Paragraph(f"- {money(opt_b.get('discount'))}" if Decimal(str(opt_b.get('discount', 0))) > 0 else "Rs. 0.00", cell_style),
            ],
            [
                Paragraph("<b>TOTAL ON-ROAD FINAL PRICE</b>", cell_bold),
                Paragraph(f"<b>{money(opt_a.get('final_price'))}</b>", cell_bold),
                Paragraph(f"<b>{money(opt_b.get('final_price'))}</b>", cell_bold),
            ],
        ]

        cmp_table = Table(cmp_rows, colWidths=[160, 190, 190])
        cmp_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ("BACKGROUND", (0, 7), (-1, 7), colors.HexColor("#EBF8FF")),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(cmp_table)

        # Price comparison callout
        diff = Decimal(str(opt_b.get('final_price', 0))) - Decimal(str(opt_a.get('final_price', 0)))
        if diff != Decimal("0.00"):
            cheaper_letter = "A" if diff > Decimal("0") else "B"
            cheaper_name = opt_a.get('full_name') if diff > Decimal("0") else opt_b.get('full_name')
            story.append(Spacer(1, 6))
            diff_text = f"<b>Vehicle Option {cheaper_letter} ({cheaper_name}) is {money(abs(diff))} cheaper on-road.</b>"
            story.append(Paragraph(diff_text, cell_bold))

        story.append(Spacer(1, 8))

        # Terms & Signatures
        terms_text = (
            "<b>Terms & Conditions:</b><br/>"
            "1. These vehicles are alternative comparison options; final prices are calculated independently for each option.<br/>"
            "2. Quotation prices are valid for 15 days from the date of issuance.<br/>"
            "3. Final price at vehicle delivery is subject to statutory RTO and insurance revisions, if any.<br/>"
            "4. Booking is confirmed only upon receipt of booking deposit and chassis allocation."
        )
        story.append(Paragraph(terms_text, subtitle_style))
        story.append(Spacer(1, 12))

        sig_table = Table(
            [[Paragraph("Customer Acceptance Signature: __________________", cell_style), Paragraph("Authorized Showroom Signature: __________________", cell_style)]],
            colWidths=[270, 270],
        )
        story.append(sig_table)
        return story

    def generate_comparison_pdf(self, quotes: List[Any]) -> bytes:
        """Side-by-side comparison of 2-3 quotations for one customer, built in memory.

        `quotes` are Quotation ORM objects with customer, variant(model, brand) and items loaded.
        Figures are printed exactly as stored; nothing is recalculated here.
        """
        from io import BytesIO

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        title = ParagraphStyle("CmpTitle", parent=styles["Heading1"], fontSize=18, leading=22,
                               textColor=colors.HexColor("#1A365D"), spaceAfter=4)
        sub = ParagraphStyle("CmpSub", parent=styles["Normal"], fontSize=9, leading=12,
                             textColor=colors.HexColor("#4A5568"))
        section = ParagraphStyle("CmpSection", parent=styles["Heading2"], fontSize=11, leading=14,
                                 textColor=colors.HexColor("#2B6CB0"), spaceBefore=10, spaceAfter=4)
        cell = ParagraphStyle("CmpCell", parent=styles["Normal"], fontSize=8.5, leading=11,
                              textColor=colors.HexColor("#2D3748"))
        bold = ParagraphStyle("CmpBold", parent=cell, fontName="Helvetica-Bold", textColor=colors.HexColor("#1A202C"))

        def money(v) -> str:
            return f"Rs. {Decimal(v or 0):,.2f}"

        def vehicle(q) -> str:
            v = q.variant
            if not v:
                return f"Variant #{q.variant_id}"
            brand = v.model.brand.name if v.model and v.model.brand else ""
            model = v.model.name if v.model else ""
            return " ".join(x for x in (brand, model, v.name) if x)

        customer = quotes[0].customer
        story: List[Any] = [
            Paragraph("<b>NAVKAR TWO-WHEELER SHOWROOM</b>", title),
            Paragraph("Vehicle comparison quotation", sub),
            Paragraph(
                f"Prepared for <b>{customer.full_name}</b> ({customer.phone}) on {datetime.now():%d-%b-%Y}", sub
            ),
            Spacer(1, 10),
        ]

        n = len(quotes)
        label_w = 150
        col_w = (540 - label_w) / n
        letters = "ABC"
        head = ParagraphStyle("CmpHead", parent=bold, textColor=colors.whitesmoke)
        header = [Paragraph("<b>Item</b>", head)] + [
            Paragraph(f"<b>Vehicle {letters[i]}</b><br/>{vehicle(q)}", head) for i, q in enumerate(quotes)
        ]

        def row(label, values, strong=False):
            st = bold if strong else cell
            return [Paragraph(label, bold if strong else cell)] + [Paragraph(str(x), st) for x in values]

        def spec(q, key):
            v = q.variant
            if not v:
                return "-"
            if key == "engine":
                return f"{v.engine_cc} cc" if v.engine_cc else "-"
            return getattr(v, key, None) or "-"

        data = [
            header,
            row("Quotation number", [q.quotation_number for q in quotes]),
            row("Engine", [spec(q, "engine") for q in quotes]),
            row("Fuel / transmission", [f"{spec(q, 'fuel_type')} / {spec(q, 'transmission')}" for q in quotes]),
            row("Ex-showroom price", [money(q.base_price) for q in quotes]),
            row("Insurance", [money(q.insurance) for q in quotes]),
            row("RTO & registration", [money(q.rto) for q in quotes]),
            row("Accessories & items", [money(q.accessories) for q in quotes]),
            row("Discount", [f"- {money(q.discount)}" if q.discount else money(0) for q in quotes]),
            row("On-road final price", [money(q.final_price) for q in quotes], strong=True),
            row("Payment mode", [q.payment_mode.title() for q in quotes]),
            row("Valid until", [f"{q.expires_at:%d-%b-%Y}" if q.expires_at else "-" for q in quotes]),
        ]
        final_row = 9
        table = Table(data, colWidths=[label_w] + [col_w] * n, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ("BACKGROUND", (0, final_row), (-1, final_row), colors.HexColor("#EBF8FF")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)

        if n == 2:
            a, b = quotes
            diff = Decimal(b.final_price) - Decimal(a.final_price)
            cheaper = "A" if diff > 0 else "B"
            if diff:
                story.append(Spacer(1, 8))
                story.append(Paragraph(f"Vehicle {cheaper} is {money(abs(diff))} cheaper on-road.", bold))

        items_present = [q for q in quotes if q.items]
        if items_present:
            story.append(Paragraph("<b>Accessories and items included</b>", section))
            for i, q in enumerate(quotes):
                names = ", ".join(f"{it.item_name} ({money(it.total_price)})" for it in q.items) or "None"
                story.append(Paragraph(f"<b>Vehicle {letters[i]}:</b> {names}", cell))

        story.append(Spacer(1, 12))
        story.append(Paragraph(
            "<b>Terms:</b> Each vehicle's quotation is valid for 15 days from issue. Prices are subject to "
            "statutory RTO and insurance revisions at delivery. Individual quotation PDFs are available for each vehicle.",
            sub,
        ))
        doc.build(story)
        return buf.getvalue()
