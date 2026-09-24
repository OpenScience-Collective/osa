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

    def test_only_nemar_sets_its_own_bubble_color(self) -> None:
        """NEMAR's widget colors the reader's bubbles; every other community keeps the
        platform blue, because none of them sets user_bubble_color."""
        client = _create_test_client()
        data = client.get("/communities").json()

        by_id = {community["id"]: community["widget"] for community in data}
        assert by_id["nemar"].get("user_bubble_color") == "#5bbad5"
        others = {cid: w.get("user_bubble_color") for cid, w in by_id.items() if cid != "nemar"}
        assert others and all(color is None for color in others.values()), others

    def test_only_nemar_sets_its_own_launcher_or_label(self) -> None:
        """NEMAR is the only community on the three-icon capsule launcher (#436); every
        other community stays on the 'bubble' default and sets no launcher_label."""
        client = _create_test_client()
        data = client.get("/communities").json()

        by_id = {community["id"]: community["widget"] for community in data}
        assert by_id["nemar"].get("launcher") == "capsule"
        assert by_id["nemar"].get("launcher_label") == "Explore NEMAR"
        others = {
            cid: (w.get("launcher"), w.get("launcher_label"))
            for cid, w in by_id.items()
            if cid != "nemar"
        }
        assert others and all(value == (None, None) for value in others.values()), others

    def test_only_nemar_sets_its_own_text_colors(self) -> None:
        """NEMAR's home-page-teal widget needs dark text on its light surfaces, which
        every other community's widget does not: theme_text_color, accent_color and
        user_bubble_text_color are all NEMAR-only, checked against every real
        community's config rather than a hard-coded list of the others."""
        client = _create_test_client()
        data = client.get("/communities").json()

        by_id = {community["id"]: community["widget"] for community in data}
        assert by_id["nemar"].get("theme_text_color") == "#04121f"
        assert by_id["nemar"].get("accent_color") == "#257a92"
        assert by_id["nemar"].get("user_bubble_text_color") == "#04121f"

        others = {cid: w for cid, w in by_id.items() if cid != "nemar"}
        assert others, "expected at least one non-NEMAR community to compare against"
        for field in ("theme_text_color", "accent_color", "user_bubble_text_color"):
            leaked = {cid: w.get(field) for cid, w in others.items() if w.get(field) is not None}
            assert not leaked, f"{field} is NEMAR-only, but is also set on {leaked}"

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
