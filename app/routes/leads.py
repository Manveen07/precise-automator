import csv
import io

from fastapi import APIRouter, File, UploadFile

router = APIRouter(prefix="/api", tags=["leads"])


@router.post("/leads/upload")
async def preview_leads_upload(file: UploadFile = File(...)) -> dict:
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    errors = []
    seen: set[str] = set()
    normalized = []
    for idx, row in enumerate(rows, start=2):
        email = (row.get("email") or row.get("Email") or "").strip().lower()
        if not email:
            errors.append(f"Row {idx}: missing email")
            continue
        if email in seen:
            errors.append(f"Row {idx}: duplicate email {email}")
            continue
        seen.add(email)
        normalized.append({**row, "email": email})
    return {"filename": file.filename, "row_count": len(normalized), "validation_errors": errors[:50]}
