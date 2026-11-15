"""Sign in with USC: account matching, the login-attempt lifecycle and the routes."""

import threading
import time
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg2.errors import UniqueViolation

from app.models.auth import AccountProfile
from app.services.auth import AuthService, token_hash, usc_usernames
from app.services.usc_auth import UscLogin, handshake_origin
from tests.test_auth import StubDatabase

WHOAMI = {"Identifier": "12345", "FirstName": "Tommy", "LastName": "Trojan", "UniqueName": "ttrojan@usc.edu"}
ACCOUNT = AccountProfile(id=uuid4(), name="Tommy Trojan", username="ttrojan")


def wait_until_finished(login: UscLogin, attempt_id: str):
    for _ in range(100):
        attempt = login._attempts.get(attempt_id)
        if attempt is None or attempt.status != "waiting":
            return
        time.sleep(0.01)


def sign_in(position, identify, ready):
    """A fake popup: the student logs in, the account is found, the page may continue."""
    identify(WHOAMI)
    ready()


def test_usc_usernames_prefer_usc_username_then_brightspace_id():
    assert usc_usernames("ttrojan@usc.edu", "12345") == ["ttrojan", "usc_12345"]
    assert usc_usernames("T.Trojan", "12345") == ["t_trojan", "usc_12345"]
    assert usc_usernames("ab", "12345") == ["usc_12345"]


def test_existing_usc_account_is_found_without_creating_a_user():
    database = StubDatabase((str(uuid4()), "Tommy Trojan", "ttrojan"))
    profile = AuthService(database).find_or_create_usc_account(WHOAMI)
    assert profile.username == "ttrojan"
    sql = [call.args[0] for call in database.cursor.execute.call_args_list]
    assert "brightspace_user_id" in sql[0]
    assert database.cursor.execute.call_args_list[0].args[1] == ("12345",)
    assert not any("INSERT" in statement for statement in sql)  # no user and no session yet


def test_new_usc_account_is_created_with_brightspace_id():
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (str(uuid4()), "Tommy Trojan", "ttrojan")]
    profile = AuthService(database).find_or_create_usc_account(WHOAMI)
    assert profile.name == "Tommy Trojan"
    insert = next(c.args[1] for c in database.cursor.execute.call_args_list if "INSERT INTO public.app_users" in c.args[0])
    assert insert[1:3] == ("Tommy Trojan", "ttrojan")
    assert '"brightspace_user_id": "12345"' in insert[3]


def test_taken_username_falls_back_to_brightspace_id():
    database = StubDatabase()
    database.cursor.fetchone.side_effect = [None, (str(uuid4()), "Tommy Trojan", "usc_12345")]
    inserts = []

    def execute(sql, params=None):
        if "INSERT INTO public.app_users" in sql:
            inserts.append(params[2])
            if len(inserts) == 1:
                raise UniqueViolation()

    database.cursor.execute.side_effect = execute
    profile = AuthService(database).find_or_create_usc_account(WHOAMI)
    assert inserts == ["ttrojan", "usc_12345"]
    assert profile.username == "usc_12345"


def test_whoami_without_identifier_is_rejected():
    with pytest.raises(HTTPException) as exc:
        AuthService(StubDatabase()).find_or_create_usc_account({"UniqueName": "ttrojan"})
    assert exc.value.status_code == 502


def test_start_session_stores_only_the_token_hash():
    database = StubDatabase()
    token = AuthService(database).start_session(ACCOUNT.id, None)
    session_insert = next(c.args[1] for c in database.cursor.execute.call_args_list
                          if "INSERT INTO public.account_sessions" in c.args[0])
    assert session_insert[:2] == (token_hash(token), str(ACCOUNT.id))
    assert token not in repr(database.cursor.execute.call_args_list)


def test_only_one_login_window_at_a_time():
    release = threading.Event()
    login = UscLogin(run_login=lambda position, identify, ready: release.wait(1), find_account=lambda w: ACCOUNT)
    login.start()
    with pytest.raises(HTTPException) as exc:
        login.start()
    assert exc.value.status_code == 409
    release.set()








def test_setup_failure_after_sign_in_keeps_the_sign_in():
    def sign_in_then_fail_setup(position, identify, ready):
        sign_in(position, identify, ready)
        raise RuntimeError("Handshake setup failed")

    login = UscLogin(run_login=sign_in_then_fail_setup, find_account=lambda whoami: ACCOUNT)
    attempt_id = login.start()
    wait_until_finished(login, attempt_id)
    time.sleep(0.05)  # let the setup failure happen
    attempt = login.take(attempt_id)
    assert attempt.status == "done" and attempt.account == ACCOUNT


def test_account_lookup_failure_fails_the_sign_in():
    def no_account(whoami):
        raise HTTPException(502, "Brightspace did not say who logged in.")

    login = UscLogin(run_login=sign_in, find_account=no_account)
    attempt_id = login.start()
    wait_until_finished(login, attempt_id)
    assert login.take(attempt_id).status == "failed"


@pytest.mark.parametrize("username,accepted", [("someone_else@usc.edu", False), ("1234567890", True), ("ttrojan@usc.edu", True)])
def test_google_identity_is_checked_before_account_sessions_are_saved(username, accepted):
    find_account = MagicMock(return_value=ACCOUNT)
    saved_for = []
    whoami = {**WHOAMI, "UniqueName": username}

    def popup(position, identify, ready):
        account = identify(whoami)
        saved_for.append(account.id)
        ready()

    login = UscLogin(run_login=popup, find_account=find_account)
    attempt_id = login.start(expected_email="ttrojan@usc.edu")
    wait_until_finished(login, attempt_id)
    attempt = login.take(attempt_id)
    if accepted:
        assert attempt.status == "done"
        assert attempt.account == ACCOUNT and attempt.whoami == whoami
        assert saved_for == [ACCOUNT.id]
        find_account.assert_called_once_with(whoami)
    else:
        assert attempt.status == "failed" and "different USC accounts" in attempt.detail
        assert saved_for == []
        find_account.assert_not_called()


def test_handshake_origin_accepts_only_handshake_pages():
    assert handshake_origin("https://usc.joinhandshake.com/stu/postings?x=1") == "https://usc.joinhandshake.com"
    assert handshake_origin("https://shibboleth.usc.edu/idp/profile") is None
    assert handshake_origin("http://usc.joinhandshake.com/") is None
    assert handshake_origin("https://joinhandshake.com.evil.example/") is None




