import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.platform import schema_ready
from app.routers import admin, public
from app.scheduler import start_scheduler, stop_scheduler
from app.templating import templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Schulfest-Helfer-App", docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="session",
    max_age=3600 * 8,
    https_only=settings.BASE_URL.startswith("https://"),
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(public.router)
app.include_router(admin.router)

@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
def ready():
    from fastapi.responses import JSONResponse
    healthy = schema_ready()
    return JSONResponse({"status": "ready" if healthy else "migration-required"},
                        status_code=200 if healthy else 503)


@app.on_event("startup")
async def startup():
    start_scheduler()


@app.on_event("shutdown")
async def shutdown():
    stop_scheduler()


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(request, "errors/404.html", {}, status_code=404)


@app.exception_handler(400)
async def bad_request(request: Request, exc):
    """Abgelaufene Formulare (CSRF) als lesbare Seite statt als JSON."""
    detail = getattr(exc, "detail", None)
    return templates.TemplateResponse(
        request,
        "errors/400.html",
        {"detail": detail if isinstance(detail, str) else None},
        status_code=400,
    )


@app.exception_handler(status.HTTP_303_SEE_OTHER)
async def redirect_handler(request: Request, exc):
    location = exc.headers.get("Location", "/admin/login")
    return RedirectResponse(location, status_code=303)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.url.path not in ("/health/live", "/health/ready") and not schema_ready():
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "migration-required"}, status_code=503)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response
