from __future__ import annotations

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from study_assistant.api.routes import router
from study_assistant.core.config import get_settings
from study_assistant.db.session import SessionFactory, init_db
from study_assistant.services.assistant import StudyAssistantService
from study_assistant.services.assistant_brain import AssistantBrain
from study_assistant.services.message_interpreter import MessageInterpreterService
from study_assistant.services.openai_client import OpenAIAssistantClient
from study_assistant.services.planning import HeuristicPlanningService, PlanningService
from study_assistant.services.response_composer import ResponseComposer
from study_assistant.services.task_executor import TaskExecutor
from study_assistant.services.telegram import TelegramBotClient
from study_assistant.services.weekly_report_service import WeeklyReportService


def build_service() -> StudyAssistantService:
    settings = get_settings()
    openai_client = OpenAIAssistantClient(settings)
    planning_service = PlanningService(
        heuristic=HeuristicPlanningService(settings.timezone),
        openai_client=openai_client,
    )
    interpreter = MessageInterpreterService(openai_client=openai_client)
    assistant_brain = AssistantBrain(message_interpreter=interpreter)
    telegram_client = TelegramBotClient(settings)
    response_composer = ResponseComposer()
    task_executor = TaskExecutor(settings.timezone)
    weekly_report_service = WeeklyReportService(settings.timezone)
    return StudyAssistantService(
        settings=settings,
        session_factory=SessionFactory,
        planning_service=planning_service,
        message_interpreter=interpreter,
        telegram_client=telegram_client,
        openai_client=openai_client,
        assistant_brain=assistant_brain,
        response_composer=response_composer,
        task_executor=task_executor,
        weekly_report_service=weekly_report_service,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()
    service = build_service()
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    scheduler.add_job(service.run_due_scan, "interval", seconds=settings.scanner_interval_seconds, id="due_scan")
    scheduler.add_job(service.send_daily_summaries, "cron", hour=7, minute=0, id="daily_summary")
    scheduler.add_job(
        service.send_weekly_planning_prompts,
        "cron",
        day_of_week="sun",
        hour=19,
        minute=0,
        id="weekly_prompt",
    )
    scheduler.add_job(
        service.prune_historical_data,
        "cron",
        hour=0,
        minute=30,
        id="history_prune",
    )
    scheduler.start()
    await service.ensure_integrations_ready()

    app.state.study_assistant_service = service
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)
    await service.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Study Assistant", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
