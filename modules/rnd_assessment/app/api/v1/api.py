from fastapi import APIRouter
from app.api.v1.endpoints import cases, profiles, items, assessments, academic_intel, personnel

api_router = APIRouter()
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])
api_router.include_router(profiles.router, prefix="/cases", tags=["profiles"])
api_router.include_router(items.router, prefix="/cases", tags=["items"])
api_router.include_router(assessments.router, prefix="/cases", tags=["assessments"])
api_router.include_router(assessments.assessment_router, prefix="/assessments", tags=["assessments"])
api_router.include_router(academic_intel.router, prefix="/academic", tags=["academic_intel"])
api_router.include_router(personnel.router, prefix="/personnel", tags=["personnel"])
