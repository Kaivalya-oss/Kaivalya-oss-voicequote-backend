import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.session import AsyncSessionLocal
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.quotation import Quotation
from app.models.followup import Followup
from app.models.user import User
from app.models.vehicle import VehicleVariant, VehicleModel

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_NAME = "Sales Pipeline"
HEADERS = [
    "Customer ID",
    "Customer Name",
    "Phone",
    "Email",
    "Address",
    "City",
    "Brand",
    "Model",
    "Variant",
    "Quotation ID",
    "Quotation Number",
    "Quotation Amount",
    "Quotation Status",
    "Lead ID",
    "Lead Status",
    "Lead Priority",
    "Lead Source",
    "Salesperson",
    "Salesperson ID",
    "Next Follow-up",
    "Follow-up Status",
    "Follow-up Type",
    "Created At",
]

class GoogleSheetsService:
    def __init__(self, spreadsheet_id: str, credentials_path: str = None, credentials_json: str = None):
        self.spreadsheet_id = spreadsheet_id
        if not self.spreadsheet_id:
            raise ValueError("Spreadsheet ID is missing.")

        creds = None
        if credentials_path:
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=SCOPES
            )
        elif credentials_json:
            creds_dict = json.loads(credentials_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=SCOPES
            )
        else:
            raise ValueError("Google Service Account credentials are not configured.")

        self.service = build("sheets", "v4", credentials=creds)

    def _ensure_sheet_exists(self):
        try:
            sheet_metadata = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
            sheets = sheet_metadata.get("sheets", [])
            sheet_exists = any(s.get("properties", {}).get("title") == SHEET_NAME for s in sheets)

            if not sheet_exists:
                body = {
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": SHEET_NAME
                                }
                            }
                        }
                    ]
                }
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id, body=body
                ).execute()
                logger.info(f"Created new sheet tab: {SHEET_NAME}")

            # Always ensure headers and formatting are correct
            self._apply_headers_and_formatting()
        except HttpError as e:
            logger.error(f"Failed to access or create sheet: {e}")
            raise Exception("Failed to verify Google Sheet structure") from e

    def _apply_headers_and_formatting(self):
        # Write headers
        body = {
            "values": [HEADERS]
        }
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{SHEET_NAME}'!A1:W1",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        # Format header (bold, freeze top row)
        try:
            sheet_metadata = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
            sheet_id = None
            for s in sheet_metadata.get("sheets", []):
                if s.get("properties", {}).get("title") == SHEET_NAME:
                    sheet_id = s.get("properties", {}).get("sheetId")
                    break

            if sheet_id is not None:
                reqs = [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {
                                    "frozenRowCount": 1
                                }
                            },
                            "fields": "gridProperties.frozenRowCount"
                        }
                    },
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(HEADERS)
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {
                                        "bold": True
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.textFormat.bold"
                        }
                    }
                ]
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": reqs}
                ).execute()
        except HttpError as e:
            logger.warning(f"Could not apply formatting: {e}")

    async def fetch_data(self) -> List[List[Any]]:
        async with AsyncSessionLocal() as session:
            # We want to fetch all leads and quotations and match them by customer & variant.
            leads_result = await session.execute(
                select(Lead).options(
                    selectinload(Lead.customer),
                    selectinload(Lead.assigned_salesperson),
                    selectinload(Lead.interested_variant).selectinload(VehicleVariant.model),
                    selectinload(Lead.followups)
                )
            )
            leads = leads_result.scalars().all()

            quots_result = await session.execute(
                select(Quotation).options(
                    selectinload(Quotation.customer),
                    selectinload(Quotation.sales_executive),
                    selectinload(Quotation.variant).selectinload(VehicleVariant.model)
                )
            )
            quotations = quots_result.scalars().all()

            # Group by (customer_id, variant_id) to pair them.
            # If variant_id is None in lead, pair with None.
            paired: Dict[tuple, Dict] = {}

            for lead in leads:
                key = (lead.customer_id, lead.interested_variant_id)
                if key not in paired:
                    paired[key] = {"leads": [], "quots": [], "customer": lead.customer, "variant": lead.interested_variant}
                paired[key]["leads"].append(lead)
            
            for quot in quotations:
                key = (quot.customer_id, quot.variant_id)
                if key not in paired:
                    paired[key] = {"leads": [], "quots": [], "customer": quot.customer, "variant": quot.variant}
                paired[key]["quots"].append(quot)

            rows = []
            
            def _get_latest_followup(lead_obj):
                if not lead_obj.followups:
                    return None
                # Sort by scheduled_date descending, fallback to created_at
                return sorted(lead_obj.followups, key=lambda f: f.scheduled_date or f.created_at, reverse=True)[0]

            def _build_row(c, v, l=None, q=None):
                row = [""] * len(HEADERS)
                row[0] = c.id if c else ""
                row[1] = c.full_name if c else ""
                row[2] = c.phone if c else ""
                row[3] = c.email if c else ""
                row[4] = c.address if c else ""
                row[5] = c.city if c else ""

                if v:
                    row[6] = str(v.model.brand) if v.model and v.model.brand else ""
                    row[7] = v.model.name if v.model else ""
                    row[8] = v.name
                
                if q:
                    row[9] = q.id
                    row[10] = q.quotation_number
                    row[11] = float(q.final_price) if q.final_price else 0.0
                    row[12] = q.status
                    row[22] = q.created_at.isoformat() if q.created_at else ""

                if l:
                    row[13] = l.id
                    row[14] = l.status
                    row[15] = l.priority
                    row[16] = l.source
                    row[17] = l.assigned_salesperson.full_name if l.assigned_salesperson else ""
                    row[18] = l.assigned_salesperson_id if l.assigned_salesperson_id else ""
                    row[19] = l.next_followup_date.isoformat() if l.next_followup_date else ""
                    
                    latest_f = _get_latest_followup(l)
                    if latest_f:
                        row[20] = latest_f.status
                        row[21] = latest_f.type
                    
                    if not row[22] and l.created_at:
                        row[22] = l.created_at.isoformat()
                
                # If q has a salesperson and l doesn't, use q's
                if q and not l:
                    row[17] = q.sales_executive.full_name if q.sales_executive else ""
                    row[18] = q.sales_executive_id if q.sales_executive_id else ""
                
                return row

            for key, data in paired.items():
                ls = data["leads"]
                qs = data["quots"]
                c = data["customer"]
                v = data["variant"]
                
                # Cartesian product if both exist, otherwise just iterate what exists
                if ls and qs:
                    for l in ls:
                        for q in qs:
                            rows.append(_build_row(c, v, l, q))
                elif ls:
                    for l in ls:
                        rows.append(_build_row(c, v, l, None))
                elif qs:
                    for q in qs:
                        rows.append(_build_row(c, v, None, q))

            return rows

    async def full_sync(self):
        try:
            self._ensure_sheet_exists()
            rows = await self.fetch_data()
            
            # Clear existing data below header
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{SHEET_NAME}'!A2:W"
            ).execute()

            if rows:
                body = {
                    "values": rows
                }
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'{SHEET_NAME}'!A2",
                    valueInputOption="USER_ENTERED",
                    body=body
                ).execute()

            logger.info(f"Successfully synced {len(rows)} rows to Google Sheets.")
            return {"success": True, "rows_synced": len(rows)}
        except Exception as e:
            logger.error(f"Google Sheets sync failed: {e}")
            return {"success": False, "error": str(e)}
