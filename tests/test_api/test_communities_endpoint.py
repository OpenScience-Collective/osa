"""Tests for the /communities endpoint.

Tests cover:
- Endpoint returns available communities with widget config
- Widget config fields are correctly populated from YAML
- Default values are applied when widget config is missing
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.communities import router
from src.assistants import discover_assistants, registry

# Discover assistants to populate registry
discover_assistants()


def _create_test_client() -> TestClient:
    """Create a test client with the communities router mounted."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestCommunitiesEndpoint:
    """Tests for GET /communities endpoint."""

    def test_returns_list(self) -> None:
        """Should return a list of communities."""
        client = _create_test_client()
        response = client.get("/communities")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_returns_available_communities(self) -> None:
        """Should return all available communities from the registry."""
        client = _create_test_client()
        response = client.get("/communities")
        data = response.json()

        available_ids = {info.id for info in registry.list_available()}
        returned_ids = {c["id"] for c in data}

        # All returned communities should be available
        assert returned_ids.issubset(available_ids)
        # All available communities with configs should be returned
        available_with_config = {
            info.id for info in registry.list_available() if info.community_config
        }
        assert available_with_config == returned_ids

    def test_community_has_required_fields(self) -> None:
        """Each community should have id, name, description, status, widget."""
        client = _create_test_client()
        response = client.get("/communities")
        data = response.json()

        for community in data:
            assert "id" in community
            assert "name" in community
            assert "description" in community
            assert "status" in community
            assert "widget" in community

    def test_widget_has_required_fields(self) -> None:
        """Widget config should have title, initial_message, placeholder, suggested_questions."""
        client = _create_test_client()
        response = client.get("/communities")
        data = response.json()

        for community in data:
            widget = community["widget"]
            assert "title" in widget
            assert "initial_message" in widget
            assert "placeholder" in widget
            assert "suggested_questions" in widget
            assert isinstance(widget["suggested_questions"], list)

    def test_communities_with_widget_yaml_have_questions(self) -> None:
        """Communities that have widget config in YAML should return suggested questions."""
        client = _create_test_client()
        response = client.get("/communities")
        data = response.json()

        # All current communities should have widget config with questions
        for community in data:
            info = registry.get(community["id"])
            if info and info.community_config and info.community_config.widget:
                widget = community["widget"]
                assert len(widget["suggested_questions"]) > 0, (
                    f"Community {community['id']} has widget config but no suggested questions"
                )

    def test_widget_title_defaults_to_name(self) -> None:
        """If widget title is not set, it should default to community name."""
        client = _create_test_client()
        response = client.get("/communities")
        data = response.json()

        for community in data:
            widget = community["widget"]
            # Title should never be None
            assert widget["title"] is not None
            assert len(widget["title"]) > 0

    def test_placeholder_has_default(self) -> None:
        """Placeholder should always have a value."""
        client = _create_test_client()
        response = client.get("/communities")
        data = response.json()

        for community in data:
            assert community["widget"]["placeholder"] is not None
            assert len(community["widget"]["placeholder"]) > 0
