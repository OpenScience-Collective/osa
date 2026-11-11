"""`expect_session` makes a /chat refuse, rather than silently start empty, a session the server lost.

`POST /{community}/chat` is driven through the ASGI app, with the real router, the real
`create_community_assistant`, the real graph, the real session store and the real SSE
assembly. The model is the only stand-in: it replies with a fixed sentence, and what
these tests measure is the session store and the status the endpoint answers, neither of
which the model touches. The 409 body is compared with
`tests/fixtures/session_unknown_409.json`, which the widget's test reads too, so the two
sides cannot drift apart.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.routers.community as community_module
from src.api.routers.community import (
    SESSION_TTL_HOURS,
    SESSION_UNKNOWN_CODE,
    ChatSession,
    _get_session_store,
    create_community_router,
)
from src.assistants.registry import registry
from src.core.config.community import CommunityConfig
from src.metrics.db import init_metrics_db, metrics_connection
from src.metrics.middleware import MetricsMiddleware
from tests.helpers.chat_models import StreamingScriptedChatModel
from tests.helpers.provider_replies import text_chunk

COMMUNITY = "expectsessiontest"
OTHER_COMMUNITY = "expectsessionother"
ORIGIN = "https://expect-session.example"
BYOK = {"X-Anthropic-API-Key": "sk-ant-expect-session-test-key"}
FIXTURE = Path(__file__).parent.parent / "fixtures" / "session_unknown_409.json"
EARLIER = [
    {"role": "user", "text": "find datasets with face stimuli"},
    {"role": "assistant", "text": "The closest match is xx099904."},
]


class FixedReply(StreamingScriptedChatModel):
    """Replies with one fixed sentence, whatever it is asked."""

    chunk_script: list[list] = [[text_chunk("Here is the answer.")]]


def _config(community_id: str) -> CommunityConfig:
    return CommunityConfig(
        id=community_id,
        name="Expect Session Test",
        description="Drives the refusal of a session the server lost",
        cors_origins=[ORIGIN],
    )


@pytest.fixture(autouse=True)
def _communities():
    for community_id in (COMMUNITY, OTHER_COMMUNITY):
        registry.register_from_config(_config(community_id))
        _get_session_store(community_id).clear()
    yield
    for community_id in (COMMUNITY, OTHER_COMMUNITY):
        registry._assistants.pop(community_id, None)
        _get_session_store(community_id).clear()


@pytest.fixture(autouse=True)
def _model(monkeypatch) -> FixedReply:
    model = FixedReply()
    monkeypatch.setattr(community_module, "create_anthropic_llm", lambda **_kw: model)
    return model


@pytest.fixture(autouse=True)
def _metrics_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    init_metrics_db()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)
    app.include_router(create_community_router(COMMUNITY))
    return TestClient(app)


@pytest.fixture
def store() -> dict[str, ChatSession]:
    return _get_session_store(COMMUNITY)


def post(client: TestClient, **body):
    payload = {"message": "and how many participants", **body}
    return client.post(f"/{COMMUNITY}/chat", json=payload, headers={"Origin": ORIGIN, **BYOK})


def keep_session(store: dict, session_id: str, community_id: str = COMMUNITY) -> ChatSession:
    session = ChatSession(session_id, community_id)
    session.add_user_message("find datasets with face stimuli")
    session.add_assistant_message("The closest match is xx099904.")
    store[session_id] = session
    return session


def logged_statuses() -> list[int]:
    """The status of every row the metrics middleware and the handlers wrote."""
    with metrics_connection() as conn:
        return [r[0] for r in conn.execute("SELECT status_code FROM request_log ORDER BY id")]


def contract() -> dict:
    return json.loads(FIXTURE.read_text())


class TestAnUnknownSession:
    def test_answers_409_with_the_contract_body(self, client) -> None:
        response = post(client, session_id="gone", expect_session=True)

        assert response.status_code == 409
        assert response.json() == contract()
        assert response.json()["code"] == SESSION_UNKNOWN_CODE

    def test_creates_no_session(self, client, store) -> None:
        post(client, session_id="gone", expect_session=True)

        assert "gone" not in store
        assert not store

    def test_never_calls_the_model(self, client, _model) -> None:
        post(client, session_id="gone", expect_session=True)
        post(client, session_id="gone", expect_session=True, stream=False)

        assert _model.calls == 0

    def test_writes_no_usage_row_so_it_is_not_counted_as_an_error(self, client) -> None:
        """A lost session is an expected recovery; no stub row of status 409 lands."""
        post(client, session_id="gone", expect_session=True)
        post(client, session_id="gone", expect_session=True, stream=False)

        assert logged_statuses() == []

    def test_detail_stays_a_plain_string(self, client) -> None:
        detail = post(client, session_id="gone", expect_session=True).json()["detail"]

        assert isinstance(detail, str)
        assert detail.endswith(".")

    def test_without_the_field_it_is_unchanged(self, client, store) -> None:
        response = post(client, session_id="gone")

        assert response.status_code == 200, response.text
        assert "gone" in store

    def test_the_field_off_is_unchanged(self, client, store) -> None:
        response = post(client, session_id="gone", expect_session=False)

        assert response.status_code == 200, response.text
        assert "gone" in store

    def test_a_request_that_does_not_stream_is_refused_too(self, client, store) -> None:
        response = post(client, session_id="gone", expect_session=True, stream=False)

        assert response.status_code == 409
        assert response.json() == contract()
        assert not store


class TestWhereTheSessionIsLost:
    def test_an_expired_session_is_refused(self, client, store) -> None:
        session = keep_session(store, "old")
        session.last_active = datetime.now(UTC) - timedelta(hours=SESSION_TTL_HOURS + 1)

        response = post(client, session_id="old", expect_session=True)

        assert response.status_code == 409
        assert response.json() == contract()
        assert "old" not in store

    def test_a_deleted_session_is_refused(self, client, store) -> None:
        keep_session(store, "deleted")
        del store["deleted"]

        response = post(client, session_id="deleted", expect_session=True)

        assert response.status_code == 409
        assert response.json() == contract()

    def test_a_session_of_another_community_is_not_this_ones(self, client) -> None:
        keep_session(_get_session_store(OTHER_COMMUNITY), "elsewhere", OTHER_COMMUNITY)

        response = post(client, session_id="elsewhere", expect_session=True)

        assert response.status_code == 409


class TestALiveSession:
    def test_continues(self, client, store) -> None:
        session = keep_session(store, "live")

        response = post(client, session_id="live", expect_session=True)

        assert response.status_code == 200, response.text
        assert store["live"] is session
        assert [m.type for m in session.messages] == ["human", "ai", "human", "ai"]

    def test_continues_without_streaming(self, client, store) -> None:
        keep_session(store, "live")

        response = post(client, session_id="live", expect_session=True, stream=False)

        assert response.status_code == 200, response.text
        assert response.json()["session_id"] == "live"


class TestPriorTurnsWithTheField:
    def test_reseeds_as_it_does_without_it(self, client, store) -> None:
        response = post(client, session_id="gone", expect_session=True, prior_turns=EARLIER)

        assert response.status_code == 200, response.text
        session = store["gone"]
        assert [m.type for m in session.messages] == ["human", "ai"]
        assert "find datasets with face stimuli" in session.messages[0].content

    def test_an_empty_list_is_no_turns(self, client, store) -> None:
        response = post(client, session_id="gone", expect_session=True, prior_turns=[])

        assert response.status_code == 409
        assert not store


class TestWithoutASessionId:
    def test_is_unprocessable(self, client, store) -> None:
        response = post(client, expect_session=True)

        assert response.status_code == 422
        assert not store

    def test_an_empty_id_is_unprocessable(self, client) -> None:
        assert post(client, session_id="", expect_session=True).status_code == 422

    def test_a_first_message_without_the_field_still_starts_a_session(self, client, store) -> None:
        response = post(client)

        assert response.status_code == 200, response.text
        assert len(store) == 1
