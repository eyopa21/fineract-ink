"""Part C: clients by client_uid."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.clients.fineract import FineractLoanGateway
from app.db import get_session
from app.deps import get_fineract
from app.middleware import correlation_id_of
from app.models import OperationType
from app.routers.idempotent import run_idempotent
from app.schemas import ClientCreate, ClientOut, InProgressOut
from app.services.clients import create_client, get_client_view

router = APIRouter(prefix="/api/clients", tags=["clients"])
SessionDep = Annotated[Session, Depends(get_session)]
FineractDep = Annotated[FineractLoanGateway, Depends(get_fineract)]


@router.post(
    "",
    response_model=ClientOut | InProgressOut,
    status_code=201,
    responses={200: {"model": ClientOut}, 202: {}, 409: {}},
)
def post_client(
    payload: ClientCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    fineract: FineractDep,
) -> dict[str, Any]:
    """Create a client. Idempotent on external_reference (Part F). client_uid is unique at the
    database level, so a different reference with the same client_uid is rejected with 409."""
    cid = correlation_id_of(request)

    def action() -> tuple[int, dict[str, Any]]:
        create_client(
            session, fineract, payload.client_uid, payload.full_name, payload.activation_date, cid
        )
        return 201, get_client_view(session, fineract, payload.client_uid, cid)

    return run_idempotent(
        session,
        response,
        OperationType.CLIENT_CREATE,
        payload.external_reference,
        payload.model_dump(),
        action,
    )


@router.get("/{client_uid}", response_model=ClientOut)
def get_client(
    client_uid: str, request: Request, session: SessionDep, fineract: FineractDep
) -> dict[str, Any]:
    return get_client_view(session, fineract, client_uid, correlation_id_of(request))
