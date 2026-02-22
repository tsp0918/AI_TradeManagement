from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import rd_case as crud
from app.schemas.rd_case import CaseItemCreate, CaseItemRead

router = APIRouter()


@router.post("/{case_id}/items", response_model=CaseItemRead)
def add_item(case_id: str, payload: CaseItemCreate, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    return crud.add_case_item(
        db,
        case_id=case_id,
        external_item_id=payload.external_item_id,
        internal_item_code=payload.internal_item_code,
        intended_use_in_project=payload.intended_use_in_project,
    )


@router.get("/{case_id}/items", response_model=list[CaseItemRead])
def list_items(case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    return crud.list_case_items(db, case_id)
