# Watch2Whoop patch of Open Wearables 0.9.0 backend/app/services/refresh_token_service.py, bind-mounted read-only over
# the file in the official image by deploy/open-wearables/compose.override.yaml (the vendor source and image are unchanged).
# Upstream file sha256 073525671cc60fe62be2db0e0a60adc0b0965741499a3412347154547ec44865 (OW 0.9.0); the test
# body_engine/tests/test_ow_refresh_replay.py fails when the pinned upstream file changes, so an OW upgrade re-checks it.
#
# The only change: replay tolerance for a rotated SDK refresh token (see _replay_rotated_sdk_token). OW rotates on every
# refresh and revokes the old token at once. When the phone never receives the answer (iOS suspends the app between the
# request and the response, the network drops), it keeps the old token, every later refresh is "Invalid or revoked" and the
# phone gets 401 forever until it is reconnected with a new invitation code. Seen in production: a phone at 401 for four
# days after one lost refresh answer. Now a revoked SDK token whose successor was never used is answered again with that
# same successor (and a fresh access token); once the successor has been used, the old token is rejected as before.
import os
import secrets
from datetime import datetime, timedelta, timezone
from logging import Logger, getLogger
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from app.config import settings
from app.database import DbSession
from app.models import RefreshToken
from app.repositories.refresh_token_repository import refresh_token_repository
from app.schemas.auth import TokenResponse, TokenType
from app.services.sdk_token_service import create_sdk_user_token
from app.utils.security import create_access_token

# How long after its rotation a lost SDK refresh token may still be answered (0 = off, upstream behaviour). A phone that lost
# the answer retries at its next background wake, but the app may not run for days; 30 days matches how long the Today
# page keeps telling a user that their phone sync stopped (body_engine sync/params.py SYNC_BROKEN_GIVE_UP).
REPLAY_GRACE = timedelta(hours=float(os.environ.get("W2W_REFRESH_REPLAY_GRACE_HOURS", "720")))
# The successor is created right after the old token is revoked, in the same request (4–20 ms apart in production).
SUCCESSOR_WINDOW = timedelta(seconds=5)


class RefreshTokenService:
    """Service for managing refresh tokens."""

    def __init__(self, log: Logger) -> None:
        self.logger = log
        self.repo = refresh_token_repository

    @staticmethod
    def _generate_refresh_token_id() -> str:
        """Generate an opaque refresh token ID with rt- prefix."""
        return f"rt-{secrets.token_hex(16)}"

    def create_sdk_refresh_token(self, db_session: DbSession, user_id: UUID, app_id: str) -> str:
        """Create a refresh token for an SDK token.

        Args:
            db_session: Database session
            user_id: The OpenWearables User ID
            app_id: The application ID that created the token

        Returns:
            The refresh token string (rt-{hex})
        """
        token_id = self._generate_refresh_token_id()
        token = RefreshToken(
            id=token_id,
            token_type=TokenType.SDK,
            user_id=user_id,
            app_id=app_id,
            developer_id=None,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            revoked_at=None,
        )
        self.repo.create(db_session, token)
        self.logger.debug(f"Created SDK refresh token for user {user_id}, app {app_id}")
        return token_id

    def create_developer_refresh_token(self, db_session: DbSession, developer_id: UUID) -> str:
        """Create a refresh token for a developer token.

        Args:
            db_session: Database session
            developer_id: The developer ID

        Returns:
            The refresh token string (rt-{hex})
        """
        token_id = self._generate_refresh_token_id()
        token = RefreshToken(
            id=token_id,
            token_type=TokenType.DEVELOPER,
            user_id=None,
            app_id=None,
            developer_id=developer_id,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            revoked_at=None,
        )
        self.repo.create(db_session, token)
        self.logger.debug(f"Created developer refresh token for developer {developer_id}")
        return token_id

    def refresh_token(self, db_session: DbSession, refresh_token_str: str) -> TokenResponse:
        """Exchange a refresh token for a new access token.

        Implements refresh token rotation: the old refresh token is revoked and
        a new one is issued with each refresh request.

        Args:
            db_session: Database session
            refresh_token_str: The refresh token string

        Returns:
            TokenResponse with new access token and new refresh token

        Raises:
            HTTPException: If the refresh token is invalid or revoked
        """
        token = self.repo.get_valid_token(db_session, refresh_token_str)
        if not token:
            replayed = self._replay_rotated_sdk_token(db_session, refresh_token_str)
            if replayed is not None:
                return replayed
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked refresh token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Revoke the old refresh token (rotation)
        self.repo.revoke_token(db_session, token)

        # Generate new access token and refresh token based on token type
        if token.token_type == TokenType.SDK:
            access_token = create_sdk_user_token(
                app_id=token.app_id,  # ty:ignore[invalid-argument-type]
                user_id=str(token.user_id),
            )
            new_refresh_token = self.create_sdk_refresh_token(
                db_session,
                user_id=token.user_id,  # ty:ignore[invalid-argument-type]
                app_id=token.app_id,  # ty:ignore[invalid-argument-type]
            )
            self.logger.debug(f"Refreshed SDK token for user {token.user_id} (rotated)")
        elif token.token_type == TokenType.DEVELOPER:
            access_token = create_access_token(subject=str(token.developer_id))
            new_refresh_token = self.create_developer_refresh_token(
                db_session,
                developer_id=token.developer_id,  # ty:ignore[invalid-argument-type]
            )
            self.logger.debug(f"Refreshed developer token for developer {token.developer_id} (rotated)")
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown token type: {token.token_type}",
            )

        return TokenResponse(
            access_token=access_token,
            token_type="bearer",
            refresh_token=new_refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )

    def _replay_rotated_sdk_token(self, db_session: DbSession, refresh_token_str: str) -> TokenResponse | None:
        """Answer a rotated SDK refresh token again if the phone evidently never got the answer.

        That is: the token exists, is an SDK token, was revoked by a rotation within REPLAY_GRACE (a token revoked
        through /token/revoke has no successor), and its successor (the first token of the same user and app created
        within SUCCESSOR_WINDOW after the revocation) is still unused. The answer is that same successor plus a new access token,
        so a repeated request is idempotent. A successor that was used has been revoked itself: the old token is stale
        and stays rejected, which keeps rotation's protection against a copied token once the real phone moved on.
        """
        if REPLAY_GRACE <= timedelta(0):
            return None
        old = db_session.execute(select(RefreshToken).where(RefreshToken.id == refresh_token_str)).scalar_one_or_none()
        if old is None or old.revoked_at is None or old.token_type != TokenType.SDK or old.user_id is None:
            return None
        if datetime.now(timezone.utc) - old.revoked_at > REPLAY_GRACE:
            return None
        successor = db_session.execute(
            select(RefreshToken)
            .where(
                RefreshToken.token_type == TokenType.SDK,
                RefreshToken.user_id == old.user_id,
                RefreshToken.app_id == old.app_id,
                RefreshToken.created_at >= old.revoked_at,
                RefreshToken.created_at <= old.revoked_at + SUCCESSOR_WINDOW,
            )
            .order_by(RefreshToken.created_at)
            .limit(1)
        ).scalar_one_or_none()
        # The first token after the revocation is the successor; if the phone already used it (it is revoked), the old
        # token is stale, even if a later rotation happened to fall inside the window.
        if successor is None or successor.revoked_at is not None:
            return None
        self.logger.warning(
            f"Replayed rotated SDK refresh token for user {old.user_id}: the phone never got the previous answer "
            f"(rotated {old.revoked_at.isoformat()}), sending the unused successor again"
        )
        return TokenResponse(
            access_token=create_sdk_user_token(app_id=successor.app_id, user_id=str(successor.user_id)),  # ty:ignore[invalid-argument-type]
            token_type="bearer",
            refresh_token=successor.id,
            expires_in=settings.access_token_expire_minutes * 60,
        )

    def revoke_token(self, db_session: DbSession, refresh_token_str: str) -> bool:
        """Revoke a refresh token.

        Args:
            db_session: Database session
            refresh_token_str: The refresh token string

        Returns:
            True if the token was revoked, False if not found

        Raises:
            HTTPException: If the refresh token is not found
        """
        token = self.repo.get_valid_token(db_session, refresh_token_str)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Refresh token not found",
            )

        self.repo.revoke_token(db_session, token)
        self.logger.debug(f"Revoked refresh token {refresh_token_str[:10]}...")
        return True


refresh_token_service = RefreshTokenService(log=getLogger(__name__))
