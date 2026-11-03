"""Bereichsübergreifende Job-Übersicht (pausierte Suchen für die Header-Anzeige)."""
from fastapi import APIRouter

from services import job_overview

router = APIRouter(tags=["jobs"])


@router.get("/api/jobs/paused")
async def jobs_paused():
    jobs = job_overview.paused_jobs()
    return {"jobs": jobs, "count": len(jobs)}
