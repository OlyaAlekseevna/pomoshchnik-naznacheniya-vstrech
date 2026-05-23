from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.db.models import GoogleOAuthCredential
from app.domain.scheduling import TimeInterval

GOOGLE_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
TOKEN_EXPIRY_SKEW_SECONDS = 30

logger = logging.getLogger(__name__)


class GoogleIntegrationError(RuntimeError):
    """Base error for Google Calendar integration failures."""


class GoogleAuthRequiredError(GoogleIntegrationError):
    """Google OAuth token is missing, revoked, or expired without refresh capability."""


class GooglePermissionDeniedError(GoogleIntegrationError):
    """Google API denied access due to scope/permission issues."""


@dataclass(frozen=True)
class GoogleOAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scope: str | None
    token_type: str | None


@dataclass(frozen=True)
class GoogleCreatedEvent:
    google_event_id: str
    event_url: str | None
    created_in_google_at: datetime | None


@dataclass(frozen=True)
class GoogleEventDraft:
    summary: str
    location: str
    description: str
    start_at: datetime
    end_at: datetime
    timezone: str
    attendee_email: str


class GoogleCalendarService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_oauth_configured(self) -> bool:
        return bool(
            self._settings.google_oauth_client_id
            and self._settings.google_oauth_client_secret
        )

    def build_authorization_url(self, state: str) -> str:
        self._ensure_client_credentials()
        params = {
            "client_id": self._settings.google_oauth_client_id or "",
            "redirect_uri": self._settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": self._settings.google_oauth_scopes,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_authorization_code(self, code: str) -> GoogleOAuthTokens:
        self._ensure_client_credentials()
        payload = {
            "code": code,
            "client_id": self._settings.google_oauth_client_id or "",
            "client_secret": self._settings.google_oauth_client_secret.get_secret_value()
            if self._settings.google_oauth_client_secret is not None
            else "",
            "redirect_uri": self._settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        }
        response_json = await self._post_form(GOOGLE_OAUTH_TOKEN_URL, payload)
        tokens = self._parse_tokens(response_json)
        logger.info("Google OAuth connected.", extra={"event": "google_oauth_connected"})
        return tokens

    async def refresh_access_token(self, refresh_token: str) -> GoogleOAuthTokens:
        self._ensure_client_credentials()
        payload = {
            "refresh_token": refresh_token,
            "client_id": self._settings.google_oauth_client_id or "",
            "client_secret": self._settings.google_oauth_client_secret.get_secret_value()
            if self._settings.google_oauth_client_secret is not None
            else "",
            "grant_type": "refresh_token",
        }
        response_json = await self._post_form(GOOGLE_OAUTH_TOKEN_URL, payload, is_refresh=True)
        tokens = self._parse_tokens(response_json)
        return tokens

    async def get_valid_access_token(
        self,
        credentials: GoogleOAuthCredential | None,
    ) -> tuple[str, GoogleOAuthTokens | None]:
        if credentials is None:
            logger.warning(
                "Google OAuth requires authorization.",
                extra={
                    "event": "google_oauth_reauthorization_required",
                    "reason": "missing_credentials",
                },
            )
            raise GoogleAuthRequiredError("Google OAuth is not connected yet.")

        now = datetime.now(UTC)
        has_fresh_access_token = (
            credentials.access_token
            and credentials.access_token_expires_at
            and credentials.access_token_expires_at
            > now + timedelta(seconds=TOKEN_EXPIRY_SKEW_SECONDS)
        )
        if has_fresh_access_token:
            return credentials.access_token, None

        try:
            refreshed = await self.refresh_access_token(credentials.refresh_token)
        except GoogleAuthRequiredError:
            logger.warning(
                "Google OAuth requires reauthorization.",
                extra={
                    "event": "google_oauth_reauthorization_required",
                    "reason": "refresh_token_invalid",
                },
            )
            raise

        return refreshed.access_token, refreshed

    async def list_busy_intervals(
        self,
        access_token: str,
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> list[TimeInterval]:
        logger.info(
            "Google calendar busy request started.",
            extra={
                "event": "google_calendar_busy_request_started",
                "time_min": time_min.isoformat(),
                "time_max": time_max.isoformat(),
            },
        )
        payload = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "timeZone": timezone,
            "items": [{"id": self._settings.google_calendar_id}],
        }
        response_json = await self._post_json(
            f"{GOOGLE_CALENDAR_API_BASE_URL}/freeBusy",
            payload,
            access_token=access_token,
        )
        calendars = response_json.get("calendars", {})
        target_calendar = calendars.get(self._settings.google_calendar_id)
        if target_calendar is None and calendars:
            target_calendar = next(iter(calendars.values()))
        busy_items = target_calendar.get("busy", []) if isinstance(target_calendar, dict) else []

        intervals: list[TimeInterval] = []
        for item in busy_items:
            start_at = datetime.fromisoformat(item["start"])
            end_at = datetime.fromisoformat(item["end"])
            intervals.append(TimeInterval(start_at=start_at, end_at=end_at))

        logger.info(
            "Google calendar busy request completed.",
            extra={
                "event": "google_calendar_busy_request_completed",
                "intervals_count": len(intervals),
            },
        )
        return intervals

    async def create_event(
        self,
        access_token: str,
        draft: GoogleEventDraft,
    ) -> GoogleCreatedEvent:
        logger.info(
            "Google event creation started.",
            extra={"event": "google_event_creation_started"},
        )
        payload = {
            "summary": draft.summary,
            "location": draft.location,
            "description": draft.description,
            "start": {"dateTime": draft.start_at.isoformat(), "timeZone": draft.timezone},
            "end": {"dateTime": draft.end_at.isoformat(), "timeZone": draft.timezone},
            "attendees": [{"email": draft.attendee_email}],
        }
        response_json = await self._post_json(
            f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/{self._settings.google_calendar_id}/events",
            payload,
            access_token=access_token,
        )
        created = GoogleCreatedEvent(
            google_event_id=str(response_json["id"]),
            event_url=response_json.get("htmlLink"),
            created_in_google_at=(
                datetime.fromisoformat(response_json["created"])
                if response_json.get("created")
                else None
            ),
        )
        logger.info(
            "Google event created.",
            extra={"event": "google_event_created", "google_event_id": created.google_event_id},
        )
        return created

    def _ensure_client_credentials(self) -> None:
        if not self.is_oauth_configured():
            raise GoogleIntegrationError("Google OAuth client credentials are not configured.")

    async def _post_form(
        self,
        url: str,
        data: dict[str, str],
        is_refresh: bool = False,
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, data=data)
        except httpx.HTTPError as error:
            raise GoogleIntegrationError("Failed to reach Google OAuth endpoint.") from error

        if response.status_code in {400, 401} and is_refresh:
            logger.warning(
                "Google OAuth requires reauthorization.",
                extra={
                    "event": "google_oauth_reauthorization_required",
                    "status_code": response.status_code,
                },
            )
            raise GoogleAuthRequiredError("Google refresh token is invalid or expired.")
        if response.status_code in {400, 401}:
            raise GoogleIntegrationError("Google OAuth code exchange failed.")
        if response.status_code == 403:
            logger.error(
                "Google permission error.",
                extra={"event": "google_permission_error", "status_code": response.status_code},
            )
            raise GooglePermissionDeniedError("Google denied OAuth operation.")
        if response.status_code >= 500:
            raise GoogleIntegrationError("Google OAuth endpoint temporary unavailable.")
        if response.status_code >= 300:
            raise GoogleIntegrationError("Google OAuth request failed.")
        return response.json()

    async def _post_json(
        self,
        url: str,
        payload: dict,
        access_token: str,
    ) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as error:
            raise GoogleIntegrationError("Failed to reach Google Calendar API.") from error

        if response.status_code in {401, 400}:
            logger.warning(
                "Google OAuth requires reauthorization.",
                extra={
                    "event": "google_oauth_reauthorization_required",
                    "status_code": response.status_code,
                },
            )
            raise GoogleAuthRequiredError("Google access token is invalid.")
        if response.status_code == 403:
            logger.error(
                "Google permission error.",
                extra={"event": "google_permission_error", "status_code": response.status_code},
            )
            raise GooglePermissionDeniedError("Google permission denied.")
        if response.status_code == 409:
            raise GoogleIntegrationError("Google calendar conflict.")
        if response.status_code >= 500:
            raise GoogleIntegrationError("Google Calendar API temporary unavailable.")
        if response.status_code >= 300:
            raise GoogleIntegrationError("Google Calendar API request failed.")
        return response.json()

    @staticmethod
    def _parse_tokens(response_json: dict) -> GoogleOAuthTokens:
        access_token = response_json.get("access_token")
        if not access_token:
            raise GoogleIntegrationError("Google OAuth response does not contain access_token.")
        expires_in = response_json.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        return GoogleOAuthTokens(
            access_token=access_token,
            refresh_token=response_json.get("refresh_token"),
            expires_at=expires_at,
            scope=response_json.get("scope"),
            token_type=response_json.get("token_type"),
        )
