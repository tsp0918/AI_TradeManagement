from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import rd_case as crud
from app.schemas.rd_case import AssessmentRead, AssessmentRunRequest
from app.services.assessment_runner import run_assessment

router = APIRouter()
assessment_router = APIRouter()


@router.post("/{case_id}/assessments")
async def create_assessment(case_id: str, payload: AssessmentRunRequest, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    if not crud.get_latest_profile(db, case_id):
        raise HTTPException(status_code=400, detail="profile not found. create profile first.")

    return await run_assessment(db, case_id=case_id, performed_by_user_id=payload.performed_by_user_id)


@router.get("/{case_id}/assessments", response_model=list[AssessmentRead])
def list_assessments(case_id: str, db: Session = Depends(get_db)):
    case = crud.get_case(db, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    return crud.list_assessments_by_case(db, case_id)


@assessment_router.get("/{assessment_id}", response_model=AssessmentRead)
def get_assessment(assessment_id: str, db: Session = Depends(get_db)):
    obj = crud.get_assessment(db, assessment_id)
    if not obj:
        raise HTTPException(status_code=404, detail="assessment not found")
    return obj
