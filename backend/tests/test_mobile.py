import json
from unittest.mock import MagicMock
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.api.routes.auth import get_current_user
from app.integrations.local import get_local_connectors
from app.main import app
from app.models.auth import AccountProfile
from app.services.auth import AuthService, token_hash
from tests.test_auth import StubDatabase

OWNER = UUID(int=2)


def test_pairing_codes_are_hashed_short_lived_and_cannot_authenticate_as_sessions():
    database = StubDatabase()
    service = AuthService(database)
    code = service.create_mobile_pairing(OWNER)
    assert code.startswith('tc_pair_') and len(code) == 40
    sql, params = database.cursor.execute.call_args.args
    assert "interval '2 minutes'" in sql and params == (token_hash(code), str(OWNER))
    assert code not in repr(database.cursor.execute.call_args_list)
    with pytest.raises(HTTPException): service.current_user(code)


def test_pairing_redeem_consumes_once_before_creating_phone_session():
    database = StubDatabase((str(OWNER),))
    code = 'tc_pair_' + 'x' * 32
    token = AuthService(database).redeem_mobile_pairing(code)
    calls = database.cursor.execute.call_args_list
    assert 'DELETE FROM public.account_sessions' in calls[0].args[0]
    assert 'expires_at > now() RETURNING user_id' in calls[0].args[0]
    assert calls[0].args[1] == (token_hash(code),)
    assert any('INSERT INTO public.account_sessions' in call.args[0] for call in calls)
    assert token != code
    database.cursor.fetchone.return_value = None
    with pytest.raises(HTTPException): AuthService(database).redeem_mobile_pairing(code)


def test_mobile_courses_require_authentication_and_reject_a_model_selected_owner():
    client = TestClient(app)
    assert client.post('/mobile/semester-courses', json={'semester':'Fall 2026'}).status_code == 401
    assert client.post('/mobile/pairing').status_code == 401


def test_pairing_rejects_an_untrusted_browser_origin():
    response = TestClient(app).post('/mobile/pairing/redeem',
        json={'code':'tc_pair_' + 'x' * 32}, headers={'Origin':'https://untrusted.example'})
    assert response.status_code == 403


def test_mobile_course_read_uses_only_the_authenticated_owners_course_tool():
    app.dependency_overrides[get_current_user] = lambda: AccountProfile(id=OWNER, name='Student', username='student')
    connector = MagicMock()
    connector.call.return_value = json.dumps([{'type':'text','text':'{"courses":[]}'}])
    app.dependency_overrides[get_local_connectors] = lambda: connector
    try:
        client = TestClient(app)
        response = client.post('/mobile/semester-courses', json={'semester':'Fall 2026'})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        connector.call.assert_called_once_with(OWNER, 'brightspace__get_my_courses', {}, ['brightspace'])
        assert response.json()['semester_filter_applied'] is False
        assert client.post('/mobile/semester-courses', json={'semester':'Fall 2026','user_id':str(UUID(int=3))}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_local_connectors, None)


def test_invalid_pairing_input_does_not_echo_the_secret():
    secret = 'private-invalid-pair-code'
    result = TestClient(app).post('/mobile/pairing/redeem', json={'code':secret})
    assert result.status_code == 422 and secret not in result.text
