from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from pydantic import BaseModel
import json

from app.database import get_db
from app.services.ollama_service import OllamaService
from app.services.favorites_service import FavoritesService

router = APIRouter()


class ExtractUseCasesRequest(BaseModel):
    """Request model for extracting use cases."""
    title: str
    abstract: str
    description: Optional[str] = ""
    claims: Optional[str] = ""
    model: Optional[str] = None


@router.post("/use-cases/extract/{favorite_id}")
async def extract_use_cases_for_favorite(
    favorite_id: int,
    model: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Extract use cases from a favorite patent using Ollama.

    Args:
        favorite_id: ID of the favorite patent
        model: Optional Ollama model to use (defaults to config)
    """
    favorites_service = FavoritesService()
    ollama_service = OllamaService(model=model)

    try:
        # Get favorite patent
        favorite = await favorites_service.get_favorite_by_id(db, favorite_id)

        if not favorite:
            raise HTTPException(status_code=404, detail="Favorite patent not found")

        # Extract use cases using title + abstract + description
        # Claims are not used to keep processing time reasonable
        result = await ollama_service.extract_use_cases(
            title=favorite.title,
            abstract=favorite.abstract or "",
            description=favorite.description or "",
            claims=""
        )

        if not result["success"]:
            raise HTTPException(
                status_code=500,
                detail=f"Use case extraction failed: {result.get('error', 'Unknown error')}"
            )

        # Save use cases to database
        saved_use_cases = []
        for use_case_text in result["use_cases"]:
            use_case = await favorites_service.add_use_case(
                db=db,
                patent_id=favorite_id,
                use_case_text=use_case_text,
                extraction_model=result["model"]
            )
            saved_use_cases.append({
                "id": use_case.id,
                "text": use_case.use_case_text,
                "model": use_case.extraction_model,
                "created_at": use_case.created_at.isoformat()
            })

        return {
            "message": "Use cases extracted successfully",
            "patent_id": favorite_id,
            "use_cases": saved_use_cases,
            "model": result["model"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract use cases: {str(e)}"
        )


@router.post("/use-cases/extract")
async def extract_use_cases(
    request: ExtractUseCasesRequest
):
    """
    Extract use cases from patent data (without saving to favorites).

    Request body:
    - title: Patent title
    - abstract: Patent abstract
    - description: Patent description (optional)
    - claims: Patent claims (optional)
    - model: Ollama model to use (optional)
    """
    ollama_service = OllamaService(model=request.model)

    try:
        result = await ollama_service.extract_use_cases(
            title=request.title,
            abstract=request.abstract,
            description=request.description or "",
            claims=request.claims or ""
        )

        if not result["success"]:
            raise HTTPException(
                status_code=500,
                detail=f"Use case extraction failed: {result.get('error', 'Unknown error')}"
            )

        return {
            "use_cases": result["use_cases"],
            "model": result["model"],
            "raw_response": result["raw_response"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract use cases: {str(e)}"
        )


@router.get("/use-cases/{favorite_id}")
async def get_use_cases_for_patent(
    favorite_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get all use cases for a specific patent."""
    favorites_service = FavoritesService()

    try:
        use_cases = await favorites_service.get_use_cases(db, favorite_id)

        return {
            "patent_id": favorite_id,
            "use_cases": [
                {
                    "id": uc.id,
                    "text": uc.use_case_text,
                    "model": uc.extraction_model,
                    "created_at": uc.created_at.isoformat()
                }
                for uc in use_cases
            ],
            "count": len(use_cases)
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve use cases: {str(e)}"
        )


@router.delete("/use-cases/{use_case_id}")
async def delete_use_case(
    use_case_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a specific use case."""
    favorites_service = FavoritesService()

    try:
        deleted = await favorites_service.delete_use_case(db, use_case_id)

        if deleted:
            return {"message": "Use case deleted successfully"}
        else:
            raise HTTPException(status_code=404, detail="Use case not found")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete use case: {str(e)}"
        )


@router.get("/ollama/models")
async def list_ollama_models():
    """List available Ollama models."""
    ollama_service = OllamaService()

    try:
        models = await ollama_service.list_available_models()
        return {
            "models": models,
            "count": len(models)
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list models: {str(e)}"
        )


@router.get("/ollama/status")
async def check_ollama_status():
    """Check Ollama service status."""
    ollama_service = OllamaService()

    try:
        is_connected = await ollama_service.check_connection()

        return {
            "status": "connected" if is_connected else "disconnected",
            "host": ollama_service.host,
            "model": ollama_service.model
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
