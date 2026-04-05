from __future__ import annotations

from study_assistant.models.entities import TaskStatus
from study_assistant.services.telegram import inline_keyboard


class ResponseComposer:
    def prompt_text(self, task, prompt_kind: str) -> str:
        if prompt_kind == "prep":
            return self.prep_reminder(task)
        if prompt_kind == "checkin":
            return self.checkin_prompt(task)
        if prompt_kind == "recheck":
            return self.recheck_prompt(task)
        if prompt_kind == "progress":
            return self.progress_prompt(task)
        if prompt_kind == "completion":
            return self.completion_prompt(task)
        raise ValueError(f"Unsupported prompt kind: {prompt_kind}")

    def prompt_keyboard(self, task_id: str, prompt_kind: str) -> dict | None:
        if prompt_kind in {"checkin", "recheck"}:
            return self.checkin_keyboard(task_id)
        if prompt_kind == "progress":
            return self.progress_keyboard(task_id)
        if prompt_kind == "completion":
            return self.completion_keyboard(task_id)
        return None

    def start_message(self) -> str:
        return (
            "공부 일정 비서예요.\n"
            "- /plan 으로 주간 계획 안내를 볼 수 있어요.\n"
            "- /id 로 텔레그램 ID를 확인할 수 있어요.\n"
            "- 시작 전 알림, 시작 확인, 종료 확인, 재배치를 도와드릴게요."
        )

    def plan_help_message(self) -> str:
        return (
            "주간 계획 입력은 아직 API 경로가 가장 안정적이에요.\n"
            "- 비가용 시간\n"
            "- 공부 목표\n"
            "- 바쁜 날\n"
            "이 세 가지만 정리해두면 바로 계획으로 바꿔드릴 수 있어요.\n"
            "필요하면 /id 로 텔레그램 ID부터 확인해 주세요."
        )

    def weekly_planning_prompt(self) -> str:
        return "이번 주 비가용 시간과 공부 목표를 보내주세요. /plan 을 보내면 입력 형식을 안내해드릴게요."

    def weekly_plan_message(self, draft) -> str:
        lines = ["이번 주 계획 초안을 만들었어요.", draft.summary, ""]
        for session in draft.sessions[:10]:
            lines.append(f"- {session.start_at:%m/%d %H:%M} {session.title}")
        if draft.overflow_notes:
            lines.append("")
            lines.append("추가로 시간이 더 필요한 항목:")
            lines.extend(f"- {note}" for note in draft.overflow_notes)
        return "\n".join(lines)

    def daily_summary(self, yesterday_tasks, today_tasks) -> str:
        completed = [task.title for task in yesterday_tasks if task.status == TaskStatus.COMPLETED]
        unfinished = [
            task.title
            for task in yesterday_tasks
            if task.status in {TaskStatus.MISSED, TaskStatus.PARTIAL, TaskStatus.RESCHEDULED}
        ]
        lines = []
        if completed:
            lines.append(f"어제 완료: {', '.join(completed[:3])}")
        if unfinished:
            lines.append(f"어제 미완료/변경: {', '.join(unfinished[:3])}")
        if today_tasks:
            schedule = ", ".join(f"{task.start_at:%H:%M} {task.title}" for task in today_tasks[:5])
            lines.append(f"오늘 일정: {schedule}")
        if not lines:
            lines.append("어제 기록된 일정이 없었어요. 오늘 일정을 같이 정리해볼까요?")
        return "\n".join(lines)

    def prep_reminder(self, task) -> str:
        return f"5분 뒤 '{task.title}' 시작이에요. 준비해볼까요?"

    def checkin_prompt(self, task) -> str:
        return f"지금 '{task.title}' 시작했나요?"

    def recheck_prompt(self, task) -> str:
        return f"'{task.title}' 아직 시작 못 했나요? 지금 시작 가능할까요?"

    def progress_prompt(self, task) -> str:
        return f"'{task.title}' 진행은 어때요? 너무 벅차진 않나요?"

    def completion_prompt(self, task) -> str:
        return f"빠르게 확인할게요. '{task.title}' 마무리됐어요?"

    def reschedule_prompt(self, lead_text: str) -> str:
        return f"{lead_text}\n언제로 다시 잡을까요?\n예: 오늘 6시, 내일 7시 반, 30분 뒤"

    def freeform_reschedule_help(self) -> str:
        return "좋아요. 예: 오늘 6시, 내일 7시 반, 30분 뒤처럼 말로 답장해도 돼요."

    def reschedule_confirmation(self, task, label: str) -> str:
        return f"'{task.title}'을 {label} 일정으로 옮겼어요.\n새 시간: {task.start_at:%m/%d %H:%M} - {task.end_at:%H:%M}"

    def precise_reschedule_confirmation(self, task) -> str:
        return f"좋아요. '{task.title}' 일정 다시 잡아뒀어요.\n새 시간: {task.start_at:%m/%d %H:%M} - {task.end_at:%H:%M}"

    def multiple_missed_replan_summary(self, tasks: list[object]) -> str:
        titles = [task.title for task in tasks]
        preview = ", ".join(titles[:3])
        if len(titles) > 3:
            preview += " 등"
        return (
            f"{preview} 일정은 못 한 것으로 보고 다시 배치했어요.\n"
            "오늘 남은 시간 기준으로 너무 빡세지 않게 조정해둘게요."
        )

    def weekly_report(self, report) -> str:
        completion_pct = round(report.completion_rate * 100)
        lines = [
            "이번 주 간단 리포트예요.",
            f"- 완료율: {report.completed_tasks}/{report.total_tasks} ({completion_pct}%)",
            f"- 미룬 횟수: {report.rescheduled_count}회",
        ]
        if report.best_time_window:
            lines.append(f"- 잘된 시간대: {report.best_time_window}")
        else:
            lines.append("- 잘된 시간대: 아직 데이터가 많지 않아요.")
        return "\n".join(lines)

    def checkin_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("시작했어요", f"task:{task_id}:start"), ("10분만 미룰게요", f"task:{task_id}:delay10")],
                [("오늘은 못 해요", f"task:{task_id}:skip")],
            ]
        )

    def progress_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("계속 진행 중이에요", f"task:{task_id}:progress_ok"), ("조금 버거워요", f"task:{task_id}:progress_help")],
            ]
        )

    def completion_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("완료했어요", f"task:{task_id}:done"), ("일부 했어요", f"task:{task_id}:partial")],
                [("못 했어요", f"task:{task_id}:missed")],
            ]
        )

    def reschedule_keyboard(self, task_id: str) -> dict:
        return inline_keyboard(
            [
                [("추천 시간 보여줘", f"task:{task_id}:suggest")],
                [("취소할게요", f"task:{task_id}:cancel")],
            ]
        )
