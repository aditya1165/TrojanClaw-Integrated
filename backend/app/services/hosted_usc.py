"""Managed USC sign-in. No desktop browser, shared profile, or local cookie files."""

from functools import lru_cache
from uuid import UUID, uuid4
from time import time
from fastapi import HTTPException

from app.database import get_database
from app.errors import AppError
from app.integrations.hosted import get_connectors
from app.repositories.usc_attempts import UscAttemptRepository


class UscLogin:
    def __init__(self, repository, connectors, webreg_reader=None):
        self.repository, self.connectors = repository, connectors
        self.webreg_reader = webreg_reader

    def _seal(self, attempt_id, data):
        return self.connectors._vault_factory().seal(attempt_id, "usc", "login", data)

    def _open(self, attempt_id, data):
        return self.connectors._vault_factory().open(attempt_id, "usc", "login", data)

    def start(self, binding, expected_email=None):
        browser = self.connectors._browser_factory()
        current = self.repository.current(binding)
        if current:
            attempt_id, encrypted = current
            data = self._open(attempt_id, encrypted)
            if not data.get("session_id"):
                raise HTTPException(409, "USC sign-in is still starting. Try again shortly or cancel the previous attempt.")
            return {"attempt_id": str(attempt_id), "live_url": browser.live_url(data["session_id"]),
                    "phase": "handshake" if data.get("whoami") else "brightspace"}
        attempt_id = uuid4()
        self.repository.create(attempt_id, binding, self._seal(attempt_id, {}))
        context_id, remote = None, None
        try:
            self.repository.claim(attempt_id, binding)
            context_id = browser.create_context()
            remote = browser.create_session(context_id)
            self.connectors.runtime.run("brightspace", "usc_prepare", connect_url=remote["connectUrl"])
            data = {"context_id": context_id, "session_id": remote["id"],
                    "connect_url": remote["connectUrl"], "phase": "brightspace", "expected_email": expected_email}
            live_url = browser.live_url(remote["id"])
            self.repository.save(attempt_id, binding, self._seal(attempt_id, data))
            return {"attempt_id": str(attempt_id), "live_url": live_url, "phase": "brightspace"}
        except Exception:
            try:
                if remote:
                    browser.release(remote["id"])
                if context_id:
                    browser.delete_context(context_id)
            finally:
                self.repository.delete(attempt_id, binding)
            raise

    def _cleanup(self, data):
        if not data.get("session_id") and not data.get("context_id"):
            return
        browser = self.connectors._browser_factory()
        if data.get("session_id"):
            browser.release(data["session_id"])
        retained = (data.get("context_id") and data.get("context_owner_id")
                    and self.connectors.owns_handshake_context(UUID(data["context_owner_id"]), data["context_id"]))
        if data.get("context_id") and not retained:
            browser.delete_context(data["context_id"])

    def cancel_current(self, binding):
        current = self.repository.current(binding)
        if current:
            self.cancel(current[0], binding)

    def cancel(self, attempt_id, binding):
        encrypted = self.repository.claim(attempt_id, binding)
        try:
            data = self._open(attempt_id, encrypted)
            self._cleanup(data)
            self.repository.delete(attempt_id, binding)
        except Exception:
            self.repository.unlock(attempt_id, binding)
            raise

    def finish(self, attempt_id, binding, auth, old_token, skip_handshake=False, *, issue_session=True):
        encrypted = self.repository.claim(attempt_id, binding)
        runtime = self.connectors.runtime
        try:
            data = self._open(attempt_id, encrypted)
            phase = data["phase"]
            if phase == "brightspace":
                try:
                    captured = runtime.run("brightspace", "capture", connect_url=data["connect_url"])["session"]
                except AppError as exc:
                    if exc.code not in {"login_incomplete", "verification_required"}:
                        raise
                    self.repository.unlock(attempt_id, binding)
                    return self._progress(data, "waiting", "Complete your USC login and Duo verification."), None
                # Return before any verification or provider navigation so the
                # frontend can replace the live view with the connecting popup.
                data.update(brightspace=captured, phase="verify_brightspace")
                self.repository.save(attempt_id, binding, self._seal(attempt_id, data))
                return self._progress(data, "connecting", "Connecting your USC sources…"), None

            if phase == "verify_brightspace":
                runtime.run("brightspace", "verify", session=data["brightspace"])
                data["whoami"] = runtime.run("brightspace", "identity", connect_url=data["connect_url"])["whoami"]
                if data.get("expected_email"):
                    from app.services.usc_auth import verify_brightspace_identity
                    verify_brightspace_identity(data["whoami"], data["expected_email"])
                data.update(phase="handshake", handshake_started_at=time())
                try:
                    runtime.run("handshake", "usc_handshake", connect_url=data["connect_url"])
                except AppError:
                    data["handshake_error"] = True
                self.repository.save(attempt_id, binding, self._seal(attempt_id, data))
                status = "interaction_required" if data.get("handshake_error") else "connecting"
                detail = "Handshake needs verification or can be connected later." if data.get("handshake_error") else "Connecting Handshake with your USC session…"
                return self._progress(data, status, detail), None

            if phase not in {"handshake", "verify_handshake"}:
                raise AppError("This sign-in cannot continue. Start again.", "login_expired", 409)
            if phase == "handshake" and not skip_handshake:
                try:
                    captured = runtime.run("handshake", "capture", connect_url=data["connect_url"])["session"]
                except AppError as exc:
                    if exc.code not in {"login_incomplete", "verification_required"}:
                        raise
                    self.repository.unlock(attempt_id, binding)
                    needs_help = exc.code == "verification_required" or data.get("handshake_error") or time() - data.get("handshake_started_at", 0) >= 20
                    status = "interaction_required" if needs_help else "connecting"
                    detail = "Handshake needs verification. Complete it or skip Handshake for now." if needs_help else "Connecting Handshake with your USC session…"
                    return self._progress(data, status, detail), None
                data.update(handshake=captured, phase="verify_handshake")
                self.repository.save(attempt_id, binding, self._seal(attempt_id, data))
                return self._progress(data, "connecting", "Verifying and saving your connections…"), None

            if skip_handshake:
                data.pop("handshake", None)
            elif phase == "verify_handshake":
                runtime.run("handshake", "verify", session=data["handshake"])

            if self.webreg_reader and "webreg_checked" not in data:
                # Optional, read-only WebReg uses the same authenticated USC context.
                # Checkpoint once so retries do not repeat navigation or lose a snapshot.
                snapshot = self.webreg_reader(data["connect_url"])
                if snapshot:
                    data["brightspace"]["webreg_snapshot"] = snapshot
                data["webreg_checked"] = True
                self.repository.checkpoint(attempt_id, binding, self._seal(attempt_id, data))

            if issue_session:
                profile, token = auth.usc_sign_in(data["whoami"], old_token)
            else:
                profile, token = auth.find_or_create_usc_account(data["whoami"]), None
            # Persist the verified owner before context transfer. Cancellation
            # checks the actual owner-bound record before deleting a context.
            data["context_owner_id"] = str(profile.id)
            self.repository.checkpoint(attempt_id, binding, self._seal(attempt_id, data))
            # Keep the actual USC browser for this account's Handshake login.
            # Copying only Handshake cookies loses the IdP's Shibboleth session.
            retained = False
            for provider in ("brightspace", "handshake"):
                if provider in data:
                    if provider == "handshake":
                        self.connectors.import_session(profile.id, provider, data[provider], managed_context_id=data["context_id"])
                        retained = True
                    else:
                        self.connectors.import_session(profile.id, provider, data[provider])
            if not retained:
                retained = self.connectors.import_handshake_login_context(profile.id, data["context_id"])
            cleanup = {key: value for key, value in data.items() if key != "context_id"} if retained else data
            self._cleanup(cleanup)
            self.repository.delete(attempt_id, binding)
            return {"status": "done", "account": profile, "whoami": data["whoami"]}, token
        except Exception:
            self.repository.unlock(attempt_id, binding)
            raise

    @staticmethod
    def _progress(data, status, detail):
        verified = bool(data.get("whoami"))
        return {"status": status, "phase": "handshake" if verified else "brightspace", "detail": detail,
                "connectors": [
                    {"provider": "brightspace", "state": "connected" if verified else "connecting",
                     "message": "Verified" if verified else "Connecting…"},
                    {"provider": "handshake", "state": "needs_login" if status == "interaction_required" else "connecting",
                     "message": "Verification needed" if status == "interaction_required" else "Connecting…"},
                ]}


@lru_cache(maxsize=1)
def get_usc_login():
    from app.integrations.webreg import read_remote_snapshot
    return UscLogin(UscAttemptRepository(get_database()), get_connectors(), read_remote_snapshot)
