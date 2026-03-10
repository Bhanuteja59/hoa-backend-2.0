# backend/app/core/errors.py
from __future__ import annotations

from dataclasses import dataclass
from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict | None = None


def error_response(request: Request, err: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=err.status_code,
        content={
            "request_id": request_id,
            "error": {
                "code": err.code,
                "message": err.message,
                "details": err.details or {},
            },
        },
    )
