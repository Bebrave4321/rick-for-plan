from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from study_assistant.api.dependencies import get_service
from study_assistant.schemas.contracts import (
    CreateUserRequest,
    DashboardResponse,
    PlanSubmissionRequest,
    WeeklyReportResponse,
)

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    return {"status": "ok"}


@router.post("/api/users/bootstrap")
async def bootstrap_user(payload: CreateUserRequest, service=Depends(get_service)):
    return await service.bootstrap_user(payload)


@router.post("/api/plans/weekly")
async def submit_weekly_plan(payload: PlanSubmissionRequest, service=Depends(get_service)):
    return await service.submit_weekly_plan(payload)


@router.post("/api/plans/{plan_id}/confirm")
async def confirm_weekly_plan(plan_id: str, service=Depends(get_service)):
    try:
        return await service.confirm_weekly_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/users/{telegram_user_id}/dashboard", response_model=DashboardResponse)
async def get_dashboard(telegram_user_id: int, service=Depends(get_service)):
    try:
        return await service.get_dashboard(telegram_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/users/{telegram_user_id}/weekly-report", response_model=WeeklyReportResponse)
async def get_weekly_report(telegram_user_id: int, service=Depends(get_service)):
    try:
        return await service.get_weekly_report(telegram_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/tasks/run-due-scan")
async def run_due_scan(service=Depends(get_service)):
    return await service.run_due_scan()


@router.post("/api/jobs/daily-summary")
async def run_daily_summary(service=Depends(get_service)):
    return await service.send_daily_summaries()


@router.post("/api/jobs/weekly-prompt")
async def run_weekly_prompt(service=Depends(get_service)):
    return await service.send_weekly_planning_prompts()


@router.post("/api/jobs/prune-history")
async def run_history_prune(service=Depends(get_service)):
    return await service.prune_historical_data()


@router.post("/api/telegram/webhook")
async def telegram_webhook(
    payload: dict,
    service=Depends(get_service),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    secret = service.settings.telegram_webhook_secret
    if secret and secret != x_telegram_bot_api_secret_token:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret.")
    return await service.process_telegram_update(payload)
