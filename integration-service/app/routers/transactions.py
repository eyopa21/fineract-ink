"""Part E: transaction lookup by external reference."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import TransactionOut
from app.services.transactions import get_transaction, tx_view

router = APIRouter(prefix="/api/transactions", tags=["transactions"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/{external_reference}", response_model=TransactionOut)
def get_tx(external_reference: str, session: SessionDep) -> dict[str, Any]:
    return tx_view(get_transaction(session, external_reference))
