"""Embedded high-level OAuth API built on Authlib primitives."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import OAuthServerSettings
from .models import (
    AccessTokenBundle,
    AuthorizationCodeGrant,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthUser,
    OAuthClient,
    TokenExchangeRequest,
    normalize_string_list,
    utc_now,
)
from .store import AuthStore
from .tokens import TokenSigner

try:  # pragma: no cover - optional dependency at runtime
    from authlib.oauth2.rfc6749 import AuthorizationServer as AuthlibAuthorizationServer
    from authlib.oauth2.rfc6749 import InvalidScopeError
    from authlib.oauth2.rfc6749.grants import AuthorizationCodeGrant as AuthlibAuthorizationCodeGrant
    from authlib.oauth2.rfc6749.requests import BasicOAuth2Payload, JsonRequest, OAuth2Request
    from authlib.oauth2.rfc7636 import CodeChallenge

    AUTHLIB_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - optional dependency at runtime
    AUTHLIB_AVAILABLE = False
    AuthlibAuthorizationServer = object  # type: ignore[assignment]
    AuthlibAuthorizationCodeGrant = object  # type: ignore[assignment]
    InvalidScopeError = Exception  # type: ignore[assignment]
    BasicOAuth2Payload = object  # type: ignore[assignment]
    OAuth2Request = object  # type: ignore[assignment]
    JsonRequest = object  # type: ignore[assignment]
    CodeChallenge = object  # type: ignore[assignment]


class OAuthError(ValueError):
    """Structured OAuth-style exception suitable for HTTP translation."""

    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code

    def to_dict(self) -> dict[str, str]:
        return {
            "error": self.error,
            "error_description": self.description,
        }


def build_s256_code_challenge(code_verifier: str) -> str:
    """Build an RFC 7636 S256 PKCE code challenge."""
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class ScopeResolutionError(ValueError):
    """Raised when the requested scopes cannot be resolved safely."""


def normalize_scopes(scopes: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize scope input into a unique ordered tuple."""
    return normalize_string_list(scopes)


def scope_string(scopes: str | Iterable[str] | None) -> str:
    """Convert scopes into the RFC-friendly space-delimited form."""
    return " ".join(normalize_scopes(scopes))


def expand_role_scopes(
    role_scopes: Mapping[str, Iterable[str]],
    roles: str | Iterable[str] | None,
) -> tuple[str, ...]:
    """Expand a set of roles into the scopes granted by those roles."""
    expanded: list[str] = []
    for role in normalize_scopes(roles):
        for scope in normalize_scopes(role_scopes.get(role, ())):
            if scope not in expanded:
                expanded.append(scope)
    return tuple(expanded)


def resolve_scopes(
    *,
    requested_scopes: str | Iterable[str] | None,
    client_allowed_scopes: str | Iterable[str],
    user_roles: str | Iterable[str] | None,
    role_scopes: Mapping[str, Iterable[str]],
    direct_user_scopes: str | Iterable[str] | None = None,
    baseline_scopes: str | Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Resolve the final scope set for an issued token."""
    requested = set(normalize_scopes(requested_scopes))
    client_allowed = set(normalize_scopes(client_allowed_scopes))
    user_allowed = set(expand_role_scopes(role_scopes, user_roles))
    user_allowed.update(normalize_scopes(direct_user_scopes))
    baseline = set(normalize_scopes(baseline_scopes))

    if not requested:
        final_scopes = client_allowed & user_allowed
    else:
        final_scopes = requested & client_allowed & user_allowed

    if baseline:
        if not baseline.issubset(client_allowed):
            missing = ", ".join(sorted(baseline - client_allowed))
            raise ScopeResolutionError(f"Client cannot receive required baseline scopes: {missing}")
        if not baseline.issubset(user_allowed):
            missing = ", ".join(sorted(baseline - user_allowed))
            raise ScopeResolutionError(f"User cannot receive required baseline scopes: {missing}")
        final_scopes |= baseline

    if not final_scopes:
        raise ScopeResolutionError("No scopes remain after intersecting client, user, and request policy")

    return tuple(sorted(final_scopes))


@dataclass(slots=True)
class _EmbeddedHTTPRequest:
    """Tiny framework-neutral request object used by the embedded auth server."""

    method: str
    url: str
    query: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] = field(default_factory=dict)
    json: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _EmbeddedHTTPResponse:
    """Tiny framework-neutral response object returned by Authlib."""

    status_code: int
    body: Any
    headers: dict[str, str]


class AuthorizationServerService:
    """High-level embedded OAuth API powered by Authlib primitives.

    This class keeps the public API intentionally small:

    - register users and clients
    - authorize an OAuth request for a logged-in user
    - exchange an authorization code for a JWT access token
    - expose metadata and JWKS documents

    The RFC-sensitive protocol work is delegated to Authlib's generic
    ``AuthorizationServer`` and ``AuthorizationCodeGrant`` primitives.
    """

    def __init__(
        self,
        *,
        settings: OAuthServerSettings,
        store: AuthStore,
        signer: TokenSigner,
    ) -> None:
        self.settings = settings
        self.store = store
        self.signer = signer
        self._server = self._create_authlib_server()

    def register_user(self, user: AuthUser) -> AuthUser:
        """Persist a user record in the backing store."""
        return self.store.save_user(user)

    def register_client(self, client: OAuthClient) -> OAuthClient:
        """Persist a client record in the backing store."""
        return self.store.save_client(client)

    def metadata_document(self) -> dict[str, Any]:
        """Return OAuth authorization-server metadata."""
        scopes_supported = set(normalize_scopes(self.settings.baseline_scopes))
        for scopes in self.settings.role_scopes.values():
            scopes_supported.update(normalize_scopes(scopes))

        return {
            "issuer": self.settings.issuer_url,
            "authorization_endpoint": self._absolute_url(self.settings.authorization_endpoint),
            "token_endpoint": self._absolute_url(self.settings.token_endpoint),
            "jwks_uri": self._absolute_url(self.settings.jwks_endpoint),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": list(self.settings.pkce_methods),
            "scopes_supported": sorted(scopes_supported),
        }

    def jwks_document(self) -> dict[str, Any]:
        """Return the public JWKS document exposed by the signer."""
        return self.signer.jwks_document()

    def authorize(
        self,
        request: AuthorizationRequest,
        *,
        user_id: str,
        approved_scopes: Iterable[str] | None = None,
    ) -> AuthorizationDecision:
        """Approve an authorization request for the given logged-in user."""
        user = self._require_active_user(user_id)
        approved = tuple(normalize_scopes(approved_scopes)) if approved_scopes is not None else None
        response = self._server.create_authorization_response(
            self._build_authorization_http_request(request, approved),
            grant_user=user,
        )
        return self._parse_authorization_decision(response)

    def exchange_authorization_code(self, request: TokenExchangeRequest) -> AccessTokenBundle:
        """Exchange an authorization code for a signed JWT access token."""
        response = self._server.create_token_response(self._build_token_http_request(request))
        if response.status_code >= 400:
            self._raise_response_error(response)
        if not isinstance(response.body, Mapping):
            raise OAuthError("server_error", "Token response body was not a JSON object", status_code=500)
        return AccessTokenBundle.model_validate(dict(response.body))

    def _create_authlib_server(self) -> Any:
        if not AUTHLIB_AVAILABLE:
            raise ModuleNotFoundError(
                "Authlib is not installed. Install the optional server dependencies, for example: pip install 'vibrant[mcp]'"
            )

        service = self

        class EmbeddedOAuth2Request(OAuth2Request):
            def __init__(
                self,
                *,
                method: str,
                url: str,
                headers: Mapping[str, str] | None = None,
                args: Mapping[str, str] | None = None,
                form: Mapping[str, str] | None = None,
                json: Mapping[str, Any] | None = None,
            ) -> None:
                super().__init__(method, url, headers=headers)
                self._args = dict(args or {})
                self._form = dict(form or {})
                payload = {**self._args, **self._form, **dict(json or {})}
                self.payload = BasicOAuth2Payload(payload)

            @property
            def args(self) -> dict[str, str]:
                return self._args

            @property
            def form(self) -> dict[str, str]:
                return self._form

        class _JsonPayload:
            def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
                self._payload = dict(payload or {})

            @property
            def data(self) -> dict[str, Any]:
                return self._payload

        class EmbeddedJsonRequest(JsonRequest):
            def __init__(self, *, method: str, url: str, headers: Mapping[str, str] | None = None, json: Mapping[str, Any] | None = None) -> None:
                super().__init__(method, url, headers=headers)
                self.payload = _JsonPayload(json)

        class EmbeddedAuthorizationCodeGrant(AuthlibAuthorizationCodeGrant):
            TOKEN_ENDPOINT_AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]

            def save_authorization_code(self, code: str, request: Any) -> AuthorizationCodeGrant:
                user = request.user
                if not isinstance(user, AuthUser):
                    raise InvalidScopeError(description="Authorization request did not resolve a valid user")
                approved_scopes = getattr(request, "approved_scopes", None)
                requested_scopes = normalize_scopes(request.scope)
                if approved_scopes is not None:
                    requested_scopes = tuple(sorted(set(requested_scopes) & set(normalize_scopes(approved_scopes))))
                try:
                    granted_scopes = resolve_scopes(
                        requested_scopes=requested_scopes,
                        client_allowed_scopes=request.client.allowed_scopes,
                        user_roles=user.roles,
                        role_scopes=service.settings.role_scopes,
                        direct_user_scopes=user.extra_scopes,
                        baseline_scopes=service.settings.baseline_scopes,
                    )
                except ScopeResolutionError as exc:
                    raise InvalidScopeError(description=str(exc)) from exc

                grant = AuthorizationCodeGrant(
                    code=code,
                    client_id=request.client.get_client_id(),
                    user_id=user.user_id,
                    redirect_uri=request.payload.redirect_uri,
                    granted_scopes=granted_scopes,
                    audience=getattr(request, "audience", None) or service.settings.default_audience,
                    code_challenge=request.payload.data.get("code_challenge"),
                    code_challenge_method=request.payload.data.get("code_challenge_method", "S256"),
                    state=request.payload.state,
                    expires_at=utc_now() + timedelta(seconds=service.settings.authorization_code_ttl_seconds),
                )
                service.store.save_authorization_code(grant)
                return grant

            def query_authorization_code(self, code: str, client: OAuthClient) -> AuthorizationCodeGrant | None:
                grant = service.store.get_authorization_code(code, client.get_client_id())
                if grant is None or grant.is_expired():
                    return None
                return grant

            def delete_authorization_code(self, authorization_code: AuthorizationCodeGrant) -> None:
                service.store.delete_authorization_code(authorization_code.code)

            def authenticate_user(self, authorization_code: AuthorizationCodeGrant) -> AuthUser | None:
                return service.store.get_user(authorization_code.user_id)

        class EmbeddedAuthorizationServer(AuthlibAuthorizationServer):
            def __init__(self) -> None:
                super().__init__(scopes_supported=sorted(service._supported_scopes()))
                self.register_token_generator("default", self._generate_token)
                self.register_grant(
                    EmbeddedAuthorizationCodeGrant,
                    [CodeChallenge(required=service.settings.require_pkce)],
                )

            def query_client(self, client_id: str) -> OAuthClient | None:
                return service.store.get_client(client_id)

            def save_token(self, token: dict[str, Any], request: Any) -> None:
                service.store.save_access_token(token)

            def create_oauth2_request(self, request: Any) -> EmbeddedOAuth2Request:
                if isinstance(request, EmbeddedOAuth2Request):
                    return request
                if not isinstance(request, _EmbeddedHTTPRequest):
                    raise TypeError("Embedded auth server expected an _EmbeddedHTTPRequest")
                oauth_request = EmbeddedOAuth2Request(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    args=request.query,
                    form=request.form,
                    json=request.json,
                )
                for key, value in request.extras.items():
                    setattr(oauth_request, key, value)
                return oauth_request

            def create_json_request(self, request: Any) -> EmbeddedJsonRequest:
                if isinstance(request, EmbeddedJsonRequest):
                    return request
                if not isinstance(request, _EmbeddedHTTPRequest):
                    raise TypeError("Embedded auth server expected an _EmbeddedHTTPRequest")
                return EmbeddedJsonRequest(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    json=request.json,
                )

            def handle_response(self, status: int, body: Any, headers: list[tuple[str, str]]) -> _EmbeddedHTTPResponse:
                return _EmbeddedHTTPResponse(status_code=status, body=body, headers=dict(headers))

            def send_signal(self, name: str, *args: Any, **kwargs: Any) -> None:
                return None

            def _generate_token(
                self,
                grant_type: str,
                client: OAuthClient,
                user: AuthUser | None = None,
                scope: str | None = None,
                expires_in: int | None = None,
                include_refresh_token: bool = True,
            ) -> dict[str, Any]:
                issued_at = utc_now()
                ttl = expires_in or service.settings.access_token_ttl_seconds
                expires_at = issued_at + timedelta(seconds=ttl)
                claims = {
                    "iss": service.settings.issuer_url,
                    "sub": user.user_id if user else client.get_client_id(),
                    "aud": service.settings.default_audience,
                    "exp": int(expires_at.timestamp()),
                    "iat": int(issued_at.timestamp()),
                    "client_id": client.get_client_id(),
                    "scope": scope or "",
                }
                if user is not None:
                    claims["roles"] = list(user.roles)
                    claims.update(user.claims)
                access_token = service.signer.sign(claims)
                token = {
                    "token_type": "Bearer",
                    "access_token": access_token,
                    "expires_in": ttl,
                    "scope": scope or "",
                }
                return token

        return EmbeddedAuthorizationServer()

    def _build_authorization_http_request(
        self,
        request: AuthorizationRequest,
        approved_scopes: tuple[str, ...] | None,
    ) -> _EmbeddedHTTPRequest:
        query = {
            "response_type": request.response_type,
            "client_id": request.client_id,
            "redirect_uri": request.redirect_uri,
        }
        if request.requested_scopes:
            query["scope"] = scope_string(request.requested_scopes)
        if request.state is not None:
            query["state"] = request.state
        if request.code_challenge is not None:
            query["code_challenge"] = request.code_challenge
            query["code_challenge_method"] = request.code_challenge_method
        return _EmbeddedHTTPRequest(
            method="GET",
            url=self._absolute_url(self.settings.authorization_endpoint),
            query=query,
            extras={
                "approved_scopes": approved_scopes,
                "audience": request.audience,
            },
        )

    def _build_token_http_request(self, request: TokenExchangeRequest) -> _EmbeddedHTTPRequest:
        form = {
            "grant_type": request.grant_type,
            "code": request.code,
            "client_id": request.client_id,
            "redirect_uri": request.redirect_uri,
        }
        if request.code_verifier is not None:
            form["code_verifier"] = request.code_verifier
        if request.client_secret is not None:
            form["client_secret"] = request.client_secret
        return _EmbeddedHTTPRequest(
            method="POST",
            url=self._absolute_url(self.settings.token_endpoint),
            form=form,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    def _parse_authorization_decision(self, response: _EmbeddedHTTPResponse) -> AuthorizationDecision:
        if response.status_code >= 400:
            self._raise_response_error(response)
        location = response.headers.get("Location")
        if not location:
            raise OAuthError("server_error", "Authorization response did not include a redirect URI", status_code=500)
        params = parse_qs(urlparse(location).query)
        if "error" in params:
            raise OAuthError(
                params["error"][0],
                params.get("error_description", [params["error"][0]])[0],
                response.status_code,
            )
        code = params.get("code", [None])[0]
        if not code:
            raise OAuthError("server_error", "Authorization response did not include an authorization code", status_code=500)
        grant = self.store.get_authorization_code(code)
        if grant is None:
            raise OAuthError("server_error", "Authorization code was not persisted by the store", status_code=500)
        return AuthorizationDecision(
            code=grant.code,
            redirect_uri=grant.redirect_uri,
            state=params.get("state", [None])[0],
            granted_scopes=grant.granted_scopes,
            expires_at=grant.expires_at,
        )

    def _raise_response_error(self, response: _EmbeddedHTTPResponse) -> None:
        if isinstance(response.body, Mapping):
            error = str(response.body.get("error", "server_error"))
            description = str(response.body.get("error_description", error))
            raise OAuthError(error, description, response.status_code)
        location = response.headers.get("Location")
        if location:
            params = parse_qs(urlparse(location).query)
            if "error" in params:
                raise OAuthError(
                    params["error"][0],
                    params.get("error_description", [params["error"][0]])[0],
                    response.status_code,
                )
        raise OAuthError("server_error", "OAuth request failed", response.status_code)

    def _supported_scopes(self) -> set[str]:
        scopes = set(normalize_scopes(self.settings.baseline_scopes))
        for role_scopes in self.settings.role_scopes.values():
            scopes.update(normalize_scopes(role_scopes))
        return scopes

    def _require_active_user(self, user_id: str) -> AuthUser:
        user = self.store.get_user(user_id)
        if user is None:
            raise OAuthError("access_denied", f"Unknown user: {user_id}", status_code=403)
        if not user.active:
            raise OAuthError("access_denied", f"User is inactive: {user_id}", status_code=403)
        return user

    def _absolute_url(self, path: str) -> str:
        return f"{self.settings.issuer_url.rstrip('/')}/{path.lstrip('/')}"


__all__ = [
    "AuthorizationServerService",
    "OAuthError",
    "ScopeResolutionError",
    "build_s256_code_challenge",
    "expand_role_scopes",
    "normalize_scopes",
    "resolve_scopes",
    "scope_string",
]
