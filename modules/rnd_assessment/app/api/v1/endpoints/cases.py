from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import rd_case as crud
from app.schemas.rd_case import CaseCreate, CaseRead

router = APIRouter()


@router.post("", response_model=CaseRead)
def create_case(payload: CaseCreate, db: Session = Depends(get_db)):
    return crud.create_case(
        db,
        tenant_id=payload.tenant_id,
        external_project_id=payload.external_project_id,
        title=payload.title,
        description=payload.description,
        created_by_user_id=payload.created_by_user_id,
    )


@router.get("/{case_id}", response_model=CaseRead)
def get_case(case_id: str, db: Session = Depends(get_db)):
    obj = crud.get_case(db, case_id)
    if not obj:
        raise HTTPException(status_code=404, detail="case not found")
    return obj
