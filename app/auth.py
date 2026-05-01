import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_secret_value, settings


security = HTTPBasic()


def _configured_credentials() -> tuple[str | None, str | None]:
    username = get_secret_value("APP_USERNAME") or settings.APP_USERNAME
    password = get_secret_value("APP_PASSWORD") or settings.APP_PASSWORD
    return username or None, password or None


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    expected_username, expected_password = _configured_credentials()
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="App authentication is not configured",
        )

    username_ok = secrets.compare_digest(credentials.username, expected_username)
    password_ok = secrets.compare_digest(credentials.password, expected_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
