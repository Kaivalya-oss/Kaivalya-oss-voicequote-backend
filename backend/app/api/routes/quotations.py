from datetime import datetime
import math
import os
from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.core.permissions import SALES_ROLES, ALL_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.quotation import QuotationCreate, QuotationResponse, QuotationStatusUpdate
from app.services.quotation_service import QuotationService
from app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/quotations", tags=["Quotations"])


@router.post(
    "",
    response_model=SuccessResponse[QuotationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate a comprehensive on-road vehicle quotation",
)
async def create_quotation(
    req: QuotationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = QuotationService(db)
    quote = await service.create_quotation(req, sales_executive_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=QuotationResponse.model_validate(quote))


@router.get(
    "",
    response_model=PaginatedResponse[QuotationResponse],
    summary="List, filter, and search generated quotations",
)
async def list_quotations(
    customer_id: Optional[int] = Query(None, description="Filter by customer ID"),
    sales_executive_id: Optional[int] = Query(None, description="Filter by sales executive user ID"),
    status: Optional[str] = Query(None, description="Filter by status (DRAFT, GENERATED, SENT, ACCEPTED, REJECTED, EXPIRED, CANCELLED)"),
    from_date: Optional[datetime] = Query(None, description="From creation date"),
    to_date: Optional[datetime] = Query(None, description="To creation date"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = QuotationService(db)
    quotes, total = await service.list_quotations(
        customer_id=customer_id,
        sales_executive_id=sales_executive_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        page=page,
        page_size=page_size,
    )
    pages = math.ceil(total / page_size) if total > 0 else 1
    return PaginatedResponse(
        items=[QuotationResponse.model_validate(q) for q in quotes],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{id}",
    response_model=SuccessResponse[QuotationResponse],
    summary="Get quotation details by ID",
)
async def get_quotation(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = QuotationService(db)
    quote = await service.get_quotation(id)
    return SuccessResponse(data=QuotationResponse.model_validate(quote))


@router.get(
    "/{id}/pdf",
    summary="Download or view quotation PDF document",
)
async def get_quotation_pdf(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    service = QuotationService(db)
    quote = await service.get_quotation(id)
    pdf_filename = f"{quote.quotation_number}.pdf"
    pdf_path = os.path.join(settings.PDF_STORAGE_DIR, pdf_filename)

    if not os.path.exists(pdf_path):
        # Regenerate if file missing
        pass

    if not os.path.exists(pdf_path):
        raise NotFoundException("QuotationPDF", quote.quotation_number)

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=pdf_filename,
    )


@router.put(
    "/{id}/status",
    response_model=SuccessResponse[QuotationResponse],
    summary="Update quotation status (ACCEPTED, REJECTED, CANCELLED)",
)
async def update_quotation_status(
    id: int,
    req: QuotationStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = QuotationService(db)
    updated = await service.update_status(id, req, user_id=current_user.id, ip_address=ip_address)
    return SuccessResponse(data=QuotationResponse.model_validate(updated))


@router.post(
    "/{id}/send-whatsapp",
    summary="Send quotation to customer's WhatsApp",
)
async def send_quotation_whatsapp(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    service = WhatsAppService(db)
    result = await service.send_quotation_via_whatsapp(
        quotation_id=id,
        user_id=current_user.id,
        ip_address=ip_address,
    )
    return SuccessResponse(data=result)
