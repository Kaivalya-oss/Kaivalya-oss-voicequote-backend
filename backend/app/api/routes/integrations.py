from fastapi import APIRouter, Depends, HTTPException, status
from app.api.dependencies import require_roles

from app.services.google_sheets_service import GoogleSheetsService
from app.core.config import settings
from app.core.logging import logger

router = APIRouter(prefix="/integrations/google-sheets", tags=["Integrations"])

@router.post("/sync", summary="Full sync of CRM data to Google Sheets")
async def sync_google_sheets(
    current_user = Depends(require_roles("MANAGER", "ADMIN"))
):
    try:
        service = GoogleSheetsService(
            spreadsheet_id=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
            credentials_path=settings.GOOGLE_SERVICE_ACCOUNT_FILE
        )
        result = await service.full_sync()
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Unknown error during sync")
            )
        return result
    except ValueError as e:
        logger.error(f"Configuration error for Google Sheets sync: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google Sheets integration is not configured properly."
        )
    except Exception as e:
        logger.exception("Failed to sync Google Sheets")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to perform Google Sheets sync."
        )
