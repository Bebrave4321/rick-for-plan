from __future__ import annotations

from fastapi import Request


def get_service(request: Request):
    return request.app.state.study_assistant_service
