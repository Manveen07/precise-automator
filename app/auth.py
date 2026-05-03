import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_secret_value, settings


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic(auto_error=False)
COOKIE_NAME = "precise_automator_session"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


def _configured_credentials() -> tuple[str | None, str | None]:
    username = get_secret_value("APP_USERNAME") or settings.APP_USERNAME
    password = get_secret_value("APP_PASSWORD") or settings.APP_PASSWORD
    return username or None, password or None


def _serializer() -> URLSafeTimedSerializer:
    secret_key = get_secret_value("APP_SECRET_KEY") or settings.APP_SECRET_KEY
    return URLSafeTimedSerializer(secret_key, salt="precise-automator-auth")


def _safe_next_path(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/app"
    return value


def _session_username(request: Request) -> str | None:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    try:
        payload = _serializer().loads(cookie, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    username = payload.get("username") if isinstance(payload, dict) else None
    return username if isinstance(username, str) and username else None


def _credentials_valid(username: str, password: str) -> bool:
    expected_username, expected_password = _configured_credentials()
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App authentication is not configured",
        )

    username_ok = secrets.compare_digest(username, expected_username)
    password_ok = secrets.compare_digest(password, expected_password)
    return username_ok and password_ok


def require_auth(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    username = _session_username(request)
    if username:
        return username

    if credentials and _credentials_valid(credentials.username, credentials.password):
        return credentials.username

    if credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    accept = request.headers.get("accept", "")
    if "text/html" in accept or "*/*" in accept:
        next_path = quote(request.url.path)
        raise HTTPException(status_code=303, headers={"Location": f"/login?next={next_path}"})

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


@router.get("/login")
def login_page(request: Request, next: str = "/app"):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": _safe_next_path(next), "error": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/app"),
):
    next_path = _safe_next_path(next)
    if not _credentials_valid(username, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": next_path, "error": "Invalid username or password."},
            status_code=401,
        )

    token = _serializer().dumps({"username": username})
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
