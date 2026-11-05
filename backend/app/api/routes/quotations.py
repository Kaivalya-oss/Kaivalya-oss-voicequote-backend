from datetime import datetime
import math
import os
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, require_roles
from app.core.config import settings
from app.core.exceptions import BadRequestException, NotFoundException
from app.core.permissions import SALES_ROLES, ALL_ROLES
from app.database.dependencies import get_db
from app.models.user import User
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.quotation import QuotationCreate, QuotationResponse, QuotationStatusUpdate
from app.services.access_policy import ensure_can_modify_quotation, ensure_can_view_quotation, is_scoped_reader
from app.services.pdf_service import PDFService
from app.services.quotation_service import QuotationService
from app.services.whatsapp_service import WhatsAppService
from app.services.email_service import EmailService
from pydantic import BaseModel, EmailStr, Field

router = APIRouter(prefix="/quotations", tags=["Quotations"])


@router.post(
    "",
    response_model=SuccessResponse[QuotationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate a comprehensive on-road vehicle quotation",
)
async def create_quotation(
    req: QuotationCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
    idempotency_key: Optional[str] = Header(
        None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=100,
        description="Client-generated key for one quotation attempt. Repeating a request with the same key "
        "returns the original quotation (200, Idempotent-Replayed: true) instead of creating another.",
    ),
):
    service = QuotationService(db)
    quote, replayed = await service.create_quotation(
        req, sales_executive_id=current_user.id, ip_address=ip_address, idempotency_key=idempotency_key
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
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
        sales_executive_id=current_user.id if is_scoped_reader(current_user) else sales_executive_id,
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
    "/compare/pdf",
    summary="Side-by-side comparison PDF of 2-3 quotations for the same customer",
)
async def get_comparison_pdf(
    ids: List[int] = Query(..., description="Quotation ids to compare (2-3, same customer), e.g. ?ids=12&ids=13"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    unique_ids = list(dict.fromkeys(ids))
    if not 2 <= len(unique_ids) <= 3:
        raise BadRequestException("COMPARE_COUNT", "Choose 2 or 3 different quotations to compare.")
    service = QuotationService(db)
    quotes = []
    for qid in unique_ids:
        quote = await service.get_quotation(qid)
        ensure_can_view_quotation(current_user, quote)
        quotes.append(quote)
    if len({q.customer_id for q in quotes}) != 1:
        raise BadRequestException("COMPARE_CUSTOMER_MISMATCH", "Compared quotations must belong to the same customer.")
    pdf = PDFService().generate_comparison_pdf(quotes)
    name = "comparison-" + "-vs-".join(q.quotation_number for q in quotes) + ".pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


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
    ensure_can_view_quotation(current_user, quote)
    return SuccessResponse(data=QuotationResponse.model_validate(quote))


@router.get(
    "/{id}/pdf",
    summary="Download or view quotation PDF document",
)
async def get_quotation_pdf(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = QuotationService(db)
    quote = await service.get_quotation(id)
    ensure_can_view_quotation(current_user, quote)
    pdf_filename = f"{quote.quotation_number}.pdf"
    if settings.MULTI_TENANT_ENABLED:
        pdf = PDFService().render_quotation_pdf(QuotationService.pdf_data_from_quote(quote))
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{pdf_filename}"'})
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
    ensure_can_modify_quotation(current_user, await service.get_quotation(id))
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
    ensure_can_modify_quotation(current_user, await QuotationService(db).get_quotation(id))
    service = WhatsAppService(db)
    result = await service.send_quotation_via_whatsapp(
        quotation_id=id,
        user_id=current_user.id,
        ip_address=ip_address,
    )
    return SuccessResponse(data=result)


class SendEmailRequest(BaseModel):
    email: Optional[EmailStr] = Field(
        None, description="Recipient; defaults to the customer's saved email address"
    )


@router.post(
    "/{id}/send-email",
    summary="Email the quotation PDF to the customer and notify the admin",
)
async def send_quotation_email(
    id: int,
    req: Optional[SendEmailRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*SALES_ROLES)),
    ip_address: str = Depends(get_client_ip),
):
    ensure_can_modify_quotation(current_user, await QuotationService(db).get_quotation(id))
    result = await EmailService(db).send_quotation_email(
        quotation_id=id,
        user_id=current_user.id,
        sent_by=current_user.full_name,
        to_email=str(req.email) if req and req.email else None,
        ip_address=ip_address,
    )
    return SuccessResponse(data=result)
