from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["service"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/.well-known/llms.txt", response_class=PlainTextResponse)
def well_known_llms():
    """Serve LLM directives file for automated agents."""
    base_dir = Path(__file__).resolve().parents[2]
    llms_path = base_dir / ".well-known" / "llms.txt"
    if not llms_path.exists():
        raise HTTPException(status_code=404, detail="LLM directives not available")
    return PlainTextResponse(content=llms_path.read_text(encoding="utf-8"))
