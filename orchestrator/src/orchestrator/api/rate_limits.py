"""Per-source-IP rate limits for the two routes that take a credential in the body.

    POST /enroll       10 per IP per 600 s (docs/api.md §2.2)
    POST /auth/login   20 per IP per 600 s (BVP_LOGIN_RATE_LIMIT_*)

Both are FastAPI dependencies, so they run before the body is validated:
malformed requests count against the limit too. The source IP is the last
X-Forwarded-For hop when the request comes from a trusted proxy (Caddy for
/enroll; the review UI, which passes Caddy's header on, for /auth/login).
"""

from fastapi import Depends, Request

from orchestrator.api.auth import get_context
from orchestrator.context import OrchestratorContext
from orchestrator.errors import ApiError
from orchestrator.security import SlidingWindowRateLimiter, client_ip_for_rate_limit
from shared.models import ErrorCode


def _enforce(
    request: Request, context: OrchestratorContext, limiter: SlidingWindowRateLimiter, what: str
) -> None:
    source_ip = client_ip_for_rate_limit(
        request.client.host if request.client else None,
        request.headers.get("X-Forwarded-For"),
        context.settings.trusted_proxy_networks,
    )
    wait_s = limiter.try_acquire(source_ip, context.clock.now())
    if wait_s is not None:
        raise ApiError(
            429,
            ErrorCode.RATE_LIMITED,
            f"too many {what} attempts from this address",
            {"retry_after_s": wait_s},
            headers={"Retry-After": str(wait_s)},
        )


def enforce_enroll_rate_limit(
    request: Request, context: OrchestratorContext = Depends(get_context)
) -> None:
    _enforce(request, context, context.enroll_rate_limiter, "enrollment")


def enforce_login_rate_limit(
    request: Request, context: OrchestratorContext = Depends(get_context)
) -> None:
    _enforce(request, context, context.login_rate_limiter, "sign-in")
