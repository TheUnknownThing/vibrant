"""Thin FastAPI adapter for the embedded OAuth service."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlencode

from .models import AuthUser, AuthorizationRequest, TokenExchangeRequest
from .service import AuthorizationServerService, OAuthError

CurrentUserResolver = Callable[[Any], str | AuthUser | Awaitable[str | AuthUser]]


def create_auth_app(
    service: AuthorizationServerService,
    *,
    resolve_current_user: CurrentUserResolver | None = None,
    title: str = "Vibrant Auth Server",
) -> Any:
    """Create a FastAPI app exposing the auth service.

    The hosting application is expected to provide user authentication through
    ``resolve_current_user``. That resolver should return either a user ID or an
    ``AuthUser`` model already present in the store.
    """

    try:
        from fastapi import Depends, FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse, RedirectResponse
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise ModuleNotFoundError(
            "FastAPI is not installed. Install the optional server dependencies, for example: pip install 'vibrant[mcp]'"
        ) from exc

    app = FastAPI(title=title, version="0.1.0")

    @app.exception_handler(OAuthError)
    async def _oauth_error_handler(_request: Request, exc: OAuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    async def current_user_dependency(request: Request) -> str:
        if resolve_current_user is None:
            raise HTTPException(
                status_code=501,
                detail="No user resolver has been configured for the authorization endpoint",
            )
        resolved = resolve_current_user(request)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if isinstance(resolved, AuthUser):
            return resolved.user_id
        if isinstance(resolved, str):
            return resolved
        raise HTTPException(status_code=500, detail="resolve_current_user returned an unsupported value")

    @app.get(service.settings.metadata_endpoint)
    async def metadata() -> dict[str, Any]:
        return service.metadata_document()

    @app.get(service.settings.jwks_endpoint)
    async def jwks() -> dict[str, Any]:
        return service.jwks_document()

    @app.get(service.settings.authorization_endpoint)
    async def authorize(request: Request, user_id: str = Depends(current_user_dependency)) -> RedirectResponse:
        auth_request = AuthorizationRequest(
            client_id=request.query_params["client_id"],
            redirect_uri=request.query_params["redirect_uri"],
            requested_scopes=request.query_params.get("scope", ""),
            state=request.query_params.get("state"),
            code_challenge=request.query_params.get("code_challenge"),
            code_challenge_method=request.query_params.get("code_challenge_method", "S256"),
            audience=request.query_params.get("audience"),
            response_type=request.query_params.get("response_type", "code"),
        )
        decision = service.authorize(auth_request, user_id=user_id)
        redirect_query = {"code": decision.code}
        if decision.state is not None:
            redirect_query["state"] = decision.state
        target = f"{decision.redirect_uri}?{urlencode(redirect_query)}"
        return RedirectResponse(target)

    @app.post(service.settings.token_endpoint)
    async def token(request: Request) -> JSONResponse:
        payload = await _parse_request_payload(request)
        token_request = TokenExchangeRequest.model_validate(payload)
        bundle = service.exchange_authorization_code(token_request)
        return JSONResponse(content=bundle.model_dump(exclude_none=True))

    return app


async def _parse_request_payload(request: Any) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await request.json()

    raw_body = (await request.body()).decode("utf-8")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


__all__ = ["create_auth_app"]
