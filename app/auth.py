"""Simple operator authentication (D31).

One operator account from environment variables. After login, the username is stored in a
signed, HttpOnly session cookie (Starlette SessionMiddleware). Public pages need no login.
"""
import hmac

from fastapi import HTTPException, Request

from app.config import Settings


def check_credentials(username: str, password: str, s: Settings) -> bool:
    # compare_digest takes the same time whether the first or last character differs.
    user_ok = hmac.compare_digest(username.encode(), s.operator_username.encode())
    pass_ok = hmac.compare_digest(password.encode(), s.operator_password.encode())
    return user_ok and pass_ok


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_operator(request: Request) -> str:
    """FastAPI dependency for operator JSON APIs: 401 when not logged in."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Operator login required")
    return user

