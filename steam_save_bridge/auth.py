"""Per-account Steam web-session auth.

The proven, working auth path for a self-hosted poller is the **web session
cookie** (Steam login via steampy, then call Steam's web endpoints with the
``sessionid`` / ``steamLoginSecure`` cookies) — NOT the OAuth ``access_token``
flow, which is awkward for non-Valve apps.

This module wraps a single account's ``LoginExecutor`` (from steampy) and
persists it to a pickle file, exactly the way scsd (steamCloudSaveDownloader)
does. A pickled executor is reloaded and its session re-fetched on demand, so a
long-running daemon never has to re-enter the password.
"""

from __future__ import annotations

import logging
import os
import pickle
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when a Steam account cannot authenticate or its session cannot be
    refreshed."""


@dataclass
class SteamAccount:
    """A single authenticated Steam account.

    Holds the steampy ``LoginExecutor`` (which carries the refresh token and the
    ``requests.Session`` with live cookies) and the path to the pickle file used
    to persist the session across daemon restarts.
    """

    name: str
    username: str
    password: str
    shared_secret: str = ""
    two_factor_code: str = ""
    session_file: str = ""

    def __post_init__(self) -> None:
        # Import here so that importing the package does not hard-require
        # steampy until an account is actually used.
        from steampy import LoginExecutor  # type: ignore

        self._executor_cls = LoginExecutor
        self._executor: Optional[object] = None

    # ------------------------------------------------------------------ #
    # construction / persistence
    # ------------------------------------------------------------------ #
    def _make_executor(self) -> object:
        # scsd passes the manual 2FA code into the ``shared_secret`` position and
        # monkey-patches TOTP generation to return it verbatim. We do the same so
        # that a one-time code entered by the operator is used as-is.
        from steampy import guard  # type: ignore

        code = self.two_factor_code or self.shared_secret

        executor = self._executor_cls(
            self.username,
            self.password,
            code,
            requests.Session(),
        )

        # Bypass TOTP generation: return the operator-supplied code verbatim.
        # (No-op when the account has no Steam Guard.)
        try:
            guard.generate_one_time_code = lambda *a, **k: code  # type: ignore
        except Exception:  # pragma: no cover - defensive
            pass

        return executor

    def login(self) -> "SteamAccount":
        """Perform a full login and persist the executor to ``session_file``."""
        executor = self._make_executor()
        try:
            executor.login()
        except Exception as exc:  # pragma: no cover - network/Steam side
            raise AuthError(
                f"login failed for '{self.name}': {exc}"
            ) from exc

        self._executor = executor
        self._persist()
        logger.info("logged in '%s' (steam_id=%s)", self.name, self.steam_id)
        return self

    def _persist(self) -> None:
        if not self.session_file:
            return
        self._wipe_credentials()
        os.makedirs(os.path.dirname(self.session_file) or ".", exist_ok=True)
        with open(self.session_file, "wb") as fh:
            pickle.dump(self._executor, fh)

    def _wipe_credentials(self) -> None:
        """Remove the plaintext password/username from the executor before
        persisting, so the pickle is a refresh-token vault, not a credential
        store. The refresh path (``_finalize_login`` / ``_perform_redirects`` /
        ``BeginAuthSessionViaRefreshToken``) only needs the refresh token and
        the session cookies, never the password. The account name is still
        available on ``SteamAccount.username`` for a re-login.
        """
        if self._executor is None:
            return
        self._executor.password = ""
        self._executor.username = ""

    @classmethod
    def load(cls, name: str, session_file: str) -> "SteamAccount":
        """Load a previously-persisted executor and refresh its session.

        The password is not required on the reload path — the refresh token in the
        pickled executor is enough to re-fetch a fresh web session.
        """
        if not os.path.exists(session_file):
            raise AuthError(f"no session file at {session_file}")

        with open(session_file, "rb") as fh:
            executor = pickle.load(fh)

        acct = cls(
            name=name,
            username="",
            password="",
            session_file=session_file,
        )
        acct._executor = executor
        acct.refresh()
        # Re-persist so any password left in an older pickle is purged.
        acct._persist()
        return acct

    # ------------------------------------------------------------------ #
    # session / identity
    # ------------------------------------------------------------------ #
    def refresh(self) -> "SteamAccount":
        """Re-fetch a fresh web session from the refresh token.

        Fast path first: re-run ``_finalize_login()`` then
        ``_perform_redirects()`` so the ``sessionid`` / ``steamLoginSecure``
        cookies are current (works while the ~30-day cookie is still valid).
        If that fails — e.g. after the cookie has expired — fall back to a
        full re-auth via ``BeginAuthSessionViaRefreshToken``, which mints a
        fresh session using only the ~1-year refresh token. No password is
        ever required on either path.
        """
        if self._executor is None:
            raise AuthError(f"no executor for '{self.name}'; call login() first")
        try:
            finalized = self._executor._finalize_login()
            self._executor._perform_redirects(finalized.json())
            self._executor.set_sessionid_cookies()
        except Exception:
            self._refresh_via_token()
        return self

    def _refresh_via_token(self) -> None:
        """Full re-auth using only the long-lived refresh token.

        ``BeginAuthSessionViaRefreshToken`` mints a fresh sessionid cookie (and
        a refreshed refresh token) without a password, after which we finalize
        the login and perform the web redirects exactly as the interactive path
        does.
        """
        begin = self._executor._api_call(
            'POST', 'IAuthenticationService', 'BeginAuthSessionViaRefreshToken',
            params={'refresh_token': self._executor.refresh_token},
        )
        response = begin.json()['response']
        client_id = response['client_id']
        request_id = response['request_id']

        poll = self._executor._api_call(
            'POST', 'IAuthenticationService', 'PollAuthSessionStatus',
            params={'client_id': client_id, 'request_id': request_id},
        )
        self._executor.refresh_token = poll.json()['response']['refresh_token']

        finalized = self._executor._finalize_login()
        self._executor._perform_redirects(finalized.json())
        self._executor.set_sessionid_cookies()

    @property
    def session(self) -> "requests.Session":
        """Return the live ``requests.Session`` (with valid web cookies)."""
        if self._executor is None:
            raise AuthError(f"no executor for '{self.name}'; call login() first")
        return self._executor.session  # type: ignore[attr-defined]

    @property
    def steam_id(self) -> Optional[str]:
        """The 64-bit SteamID of this account, if known."""
        if self._executor is None:
            return None
        # steampy stores it on the executor after a successful login.
        return getattr(self._executor, "steam_id", None)

    def is_logged_in(self) -> bool:
        """Cheap liveness check: does the session carry a sessionid cookie?"""
        if self._executor is None:
            return False
        try:
            session = self._executor.session  # type: ignore[attr-defined]
            return bool(session.cookies.get("sessionid"))
        except Exception:  # pragma: no cover - defensive
            return False
