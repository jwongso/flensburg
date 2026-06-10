"""Security middleware - non-overridable by jurisdictions.

Security headers and body size limits are framework-level guarantees.
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_MAX_BODY_BYTES = 20_480  # 20 KB

_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://static.cloudflareinsights.com 'sha256-v417qeH2S/efozmztGGo4VgxYIM0rH/TDoFRRfP/nPg='; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' https://cloudflareinsights.com; "
    "frame-ancestors 'none';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = _CSP
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY_BYTES:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=413,
                content={"error": f"Request body too large (max {_MAX_BODY_BYTES} bytes)."},
            )
        return await call_next(request)
