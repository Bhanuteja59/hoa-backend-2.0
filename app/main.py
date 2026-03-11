# backend/app/main.py
from __future__ import annotations

import os
import shutil
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.core.logging import configure_logging
from app.core.errors import AppError, error_response
from app.core.config import settings


# Routers
from app.api.routes import (
    auth,
    documents,
    search,
    units,
    ledger,
    work_orders,
    violations_arc,
    announcements,
    users,
    stats,
    platform,
    chatbot,
    uploads,
    payments,
)
from fastapi.staticfiles import StaticFiles

configure_logging()

# Force reload triggers (Migration to OpenAI)


app = FastAPI(title="HOA SaaS API", version="1.0.0")

# CORS Middleware (placed down to be Outer)
def setup_cors(app: FastAPI):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_origin_regex=r"https://hoa-.*\.vercel\.app",  # Support Vercel previews
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*", "x-request-id"],
    )

# Mount Uploads
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ABS_UPLOAD_DIR = os.path.join(BASE_DIR, "..", "uploads")
os.makedirs(ABS_UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=ABS_UPLOAD_DIR), name="uploads")

@app.middleware("http")
async def request_id_mw(request: Request, call_next):
    # Generate or get request ID
    req_id = request.headers.get("x-request-id") or str(uuid.uuid4())[:12]
    request.state.request_id = req_id
    
    try:
        resp = await call_next(request)
        resp.headers["x-request-id"] = req_id
        return resp
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Ensure CORS headers are present even in middleware-level crashes
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL", "message": "Critical Server Error"}},
            headers={"Access-Control-Allow-Origin": request.headers.get("origin", "*")}
        )

# Finalize CORS as outermost
setup_cors(app)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    error_msg = "Validation Error"
    if errors:
        # Extract a human-readable message from the first error
        first_error = errors[0]
        field_name = first_error.get("loc", [""])[-1]
        msg = first_error.get("msg", "")
        # Map specific field errors for a cleaner UI
        if field_name == "email":
            error_msg = "A valid email address is required (e.g. user@example.com)."
        elif field_name == "phone":
            error_msg = "Phone number must be between 10 and 15 digits with no alphabetic characters."
        else:
            error_msg = f"{field_name}: {msg}" if field_name else msg

    return JSONResponse(
        status_code=422,
        content={
            "request_id": getattr(request.state, "request_id", None),
            "error": {"code": "VALIDATION_ERROR", "message": error_msg}
        },
    )

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return error_response(request, exc)

@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "request_id": getattr(request.state, "request_id", None),
            "error": {"code": "INTERNAL", "message": str(exc) if settings.ENV == "dev" else "Internal error"},
        },
    )


# API Routes
app.include_router(auth.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")

app.include_router(units.router, prefix="/api/v1")
app.include_router(ledger.router, prefix="/api/v1")
app.include_router(work_orders.router, prefix="/api/v1")
app.include_router(violations_arc.router, prefix="/api/v1")
app.include_router(announcements.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(platform.router, prefix="/api/v1/platform")
app.include_router(platform.router, prefix="/api/v1/admin")
app.include_router(chatbot.router, prefix="/api/v1")
app.include_router(uploads.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1/payments")
