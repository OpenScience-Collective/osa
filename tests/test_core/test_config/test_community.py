"""Tests for community configuration models.

Tests cover:
- Pydantic model validation
- YAML loading and parsing
- Config serialization
"""

import warnings
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from pydantic import ValidationError

from src.api.tool_results import (
    MAX_IMAGE_EDGE_PX,
    MAX_IMAGES,
    MAX_STDERR_CHARS,
    MAX_STDOUT_CHARS,
)
from src.core.config.community import (
    MAX_CONFIGURED_CLIENT_TOOLS,
    MAX_PRELUDE_CHARS,
    BudgetConfig,
    CitationConfig,
    ClientToolConfig,
    CommunitiesConfig,
    CommunityConfig,
    DocSource,
    ExtensionsConfig,
    GitHubConfig,
    McpServer,
    NotebookConfig,
    PythonPlugin,
    PythonRuntimeConfig,
    RuntimeConfig,
    RuntimeLimits,
    WidgetConfig,
)
from src.core.config.notebook_lock import NOTEBOOK_SITE_PYODIDE_VERSION


class TestDocSource:
    """Tests for DocSource model."""

    def test_valid_doc_source(self) -> None:
        """Should create DocSource with valid inputs."""
        doc = DocSource(
            title="Example Docs",
            url="https://docs.example.com/",
            type="sphinx",
            source_repo="org/repo",
        )
        assert doc.title == "Example Docs"
        assert str(doc.url) == "https://docs.example.com/"
        assert doc.type == "sphinx"
        assert doc.source_repo == "org/repo"

    def test_doc_source_defaults(self) -> None:
        """Should use default values for optional fields."""
        doc = DocSource(title="Docs", url="https://docs.example.com/")
        assert doc.type == "html"
        assert doc.source_repo is None
        assert doc.description is None
        assert doc.preload is False
        assert doc.category == "general"

    def test_doc_source_preload_requires_source_url(self) -> None:
        """Should require source_url when preload is True."""
        with pytest.raises(ValidationError, match="preload=True but no source_url"):
            DocSource(
                title="Preloaded Docs",
                url="https://docs.example.com/",
                preload=True,
            )

        # Should pass with source_url
        doc = DocSource(
            title="Preloaded Docs",
            url="https://docs.example.com/",
            source_url="https://raw.example.com/content.md",
            preload=True,
        )
        assert doc.preload is True
        assert doc.source_url == "https://raw.example.com/content.md"

    def test_invalid_url_raises_error(self) -> None:
        """Should reject invalid URLs."""
        with pytest.raises(ValidationError):
            DocSource(title="Docs", url="not-a-url")

    def test_invalid_type_raises_error(self) -> None:
        """Should reject invalid documentation types."""
        with pytest.raises(ValidationError):
            DocSource(title="Docs", url="https://docs.example.com/", type="invalid")


class TestGitHubConfig:
    """Tests for GitHubConfig model."""

    def test_valid_github_config(self) -> None:
        """Should create GitHubConfig with valid inputs."""
        config = GitHubConfig(repos=["org/repo1", "org/repo2"])
        assert len(config.repos) == 2
        assert "org/repo1" in config.repos

    def test_empty_repos_default(self) -> None:
        """Should default to empty list."""
        config = GitHubConfig()
        assert config.repos == []

    def test_validates_repo_format(self) -> None:
        """Should validate repo format."""
        with pytest.raises(ValidationError, match="'org/repo' format"):
            GitHubConfig(repos=["invalid-repo-format"])

        with pytest.raises(ValidationError, match="'org/repo' format"):
            GitHubConfig(repos=["org/repo", "bad format"])

    def test_rejects_empty_repo_names(self) -> None:
        """Should reject empty repo names."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            GitHubConfig(repos=[""])

        with pytest.raises(ValidationError, match="cannot be empty"):
            GitHubConfig(repos=["org/repo", "  "])

    def test_deduplicates_repos(self) -> None:
        """Should deduplicate repo names."""
        config = GitHubConfig(repos=["org/repo", "org/repo", "org/other"])
        assert len(config.repos) == 2
        assert "org/repo" in config.repos
        assert "org/other" in config.repos

    def test_accepts_valid_repo_patterns(self) -> None:
        """Should accept various valid repo patterns."""
        config = GitHubConfig(
            repos=[
                "org/repo",
                "my-org/my-repo",
                "org123/repo456",
                "org.name/repo.name",
                "org_name/repo_name",
            ]
        )
        assert len(config.repos) == 5


class TestCitationConfig:
    """Tests for CitationConfig model."""

    def test_valid_citation_config(self) -> None:
        """Should create CitationConfig with valid inputs."""
        config = CitationConfig(
            queries=["query 1", "query 2"],
            dois=["10.1234/example"],
        )
        assert len(config.queries) == 2
        assert len(config.dois) == 1

    def test_empty_defaults(self) -> None:
        """Should default to empty lists."""
        config = CitationConfig()
        assert config.queries == []
        assert config.dois == []

    def test_live_search_off_by_default(self) -> None:
        """Live search is opt-in: a community must enable it explicitly."""
        assert CitationConfig().live_search is False
        assert CitationConfig(live_search=True).live_search is True

    def test_validates_doi_format(self) -> None:
        """Should validate DOI format."""
        with pytest.raises(ValidationError, match="Invalid DOI format"):
            CitationConfig(dois=["invalid-doi"])

        with pytest.raises(ValidationError, match="Invalid DOI format"):
            CitationConfig(dois=["10.1234/valid", "bad-doi"])

    def test_normalizes_doi_prefixes(self) -> None:
        """Should strip common DOI URL prefixes."""
        config = CitationConfig(
            dois=[
                "10.1234/example",
                "https://doi.org/10.5678/test",
                "http://dx.doi.org/10.9012/paper",
                "doi.org/10.3456/article",
            ]
        )
        assert "10.1234/example" in config.dois
        assert "10.5678/test" in config.dois
        assert "10.9012/paper" in config.dois
        assert "10.3456/article" in config.dois
        # Should not contain prefixes
        for doi in config.dois:
            assert not doi.startswith("http")
            assert not doi.startswith("doi.org")

    def test_deduplicates_dois(self) -> None:
        """Should deduplicate DOIs."""
        config = CitationConfig(dois=["10.1234/example", "10.1234/example", "10.5678/other"])
        assert len(config.dois) == 2
        assert "10.1234/example" in config.dois
        assert "10.5678/other" in config.dois

    def test_paper_labels_default_empty(self) -> None:
        """paper_labels defaults to an empty dict."""
        assert CitationConfig().paper_labels == {}

    def test_paper_labels_keys_normalized(self) -> None:
        """DOI keys in paper_labels are normalized like dois so they match."""
        config = CitationConfig(
            dois=["10.1234/example"],
            paper_labels={
                "https://doi.org/10.1234/example": "Example (Author 2020)",
                "doi.org/10.9012/paper": "Paper (Author 2019)",
                "10.5678/other": "Other (Author 2021)",
            },
        )
        assert config.paper_labels["10.1234/example"] == "Example (Author 2020)"
        assert config.paper_labels["10.9012/paper"] == "Paper (Author 2019)"
        assert config.paper_labels["10.5678/other"] == "Other (Author 2021)"
        for key in config.paper_labels:
            assert not key.startswith("http")
            assert not key.startswith("doi.org")

    def test_paper_labels_rejects_invalid_doi_key(self) -> None:
        """A malformed DOI key fails loudly rather than silently dropping the label."""
        with pytest.raises(ValidationError, match="Invalid DOI key in paper_labels"):
            CitationConfig(paper_labels={"not-a-doi": "Label"})

    def test_paper_labels_dedup_last_wins(self) -> None:
        """Two keys that normalize to the same DOI collapse to one (last wins)."""
        config = CitationConfig(
            paper_labels={
                "https://doi.org/10.1234/x": "Label B",
                "10.1234/x": "Label B",
            }
        )
        assert config.paper_labels == {"10.1234/x": "Label B"}

    def test_aliases_default_empty(self) -> None:
        assert CitationConfig().aliases == {}

    def test_aliases_normalizes_primary_and_versions(self) -> None:
        config = CitationConfig(
            dois=["10.1234/primary"],
            aliases={
                "https://doi.org/10.1234/primary": [
                    "https://doi.org/10.1101/preprint",
                    "10.1101/preprint",  # duplicate after normalization
                ]
            },
        )
        assert config.aliases == {"10.1234/primary": ["10.1101/preprint"]}

    def test_aliases_rejects_invalid_doi(self) -> None:
        with pytest.raises(ValidationError, match="Invalid DOI in aliases"):
            CitationConfig(dois=["10.1234/primary"], aliases={"10.1234/primary": ["not-a-doi"]})

    def test_aliases_rejects_empty_version(self) -> None:
        with pytest.raises(ValidationError, match="Empty alias version DOI"):
            CitationConfig(dois=["10.1234/primary"], aliases={"10.1234/primary": [""]})

    def test_aliases_primary_must_be_in_dois(self) -> None:
        with pytest.raises(ValidationError, match="not present in dois"):
            CitationConfig(dois=["10.1234/a"], aliases={"10.1234/b": ["10.1101/x"]})

    def test_deduplicates_queries(self) -> None:
        """Should deduplicate queries."""
        config = CitationConfig(queries=["query 1", "query 1", "query 2"])
        assert len(config.queries) == 2
        assert "query 1" in config.queries
        assert "query 2" in config.queries

    def test_removes_empty_queries(self) -> None:
        """Should remove empty queries."""
        config = CitationConfig(queries=["query 1", "", "  ", "query 2"])
        assert len(config.queries) == 2
        assert "query 1" in config.queries
        assert "query 2" in config.queries

    def test_skips_empty_dois(self) -> None:
        """Should skip empty DOI strings."""
        config = CitationConfig(dois=["10.1234/example", "", "  "])
        assert len(config.dois) == 1
        assert config.dois[0] == "10.1234/example"


class TestPythonPlugin:
    """Tests for PythonPlugin model."""

    def test_valid_plugin(self) -> None:
        """Should create PythonPlugin with valid inputs."""
        plugin = PythonPlugin(
            module="src.tools.custom",
            tools=["tool1", "tool2"],
        )
        assert plugin.module == "src.tools.custom"
        assert plugin.tools == ["tool1", "tool2"]

    def test_plugin_without_tools(self) -> None:
        """Should allow None for tools (import all)."""
        plugin = PythonPlugin(module="src.tools.custom")
        assert plugin.tools is None


class TestMcpServer:
    """Tests for McpServer model."""

    def test_valid_local_server(self) -> None:
        """Should create McpServer with command (local)."""
        server = McpServer(name="test-server", command=["uvx", "test-mcp"])
        assert server.name == "test-server"
        assert server.command == ["uvx", "test-mcp"]
        assert server.url is None

    def test_valid_remote_server(self) -> None:
        """Should create McpServer with URL (remote)."""
        server = McpServer(name="test-server", url="https://example.com/mcp")
        assert server.name == "test-server"
        assert server.url is not None
        assert server.command is None

    def test_requires_command_or_url(self) -> None:
        """Should require either command or url."""
        with pytest.raises(ValidationError, match="either 'command'.*or 'url'"):
            McpServer(name="test-server")

    def test_rejects_both_command_and_url(self) -> None:
        """Should reject both command and url."""
        with pytest.raises(ValidationError, match="cannot have both"):
            McpServer(
                name="test-server",
                command=["uvx", "test"],
                url="https://example.com/mcp",
            )

    def test_rejects_empty_command(self) -> None:
        """Should reject empty command list."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            McpServer(name="test-server", command=[])

    def test_rejects_empty_command_parts(self) -> None:
        """Should reject empty strings in command."""
        with pytest.raises(ValidationError, match="cannot be empty strings"):
            McpServer(name="test-server", command=["uvx", ""])

        with pytest.raises(ValidationError, match="cannot be empty strings"):
            McpServer(name="test-server", command=["", "test"])


class TestExtensionsConfig:
    """Tests for ExtensionsConfig model."""

    def test_valid_extensions(self) -> None:
        """Should create ExtensionsConfig with valid inputs."""
        config = ExtensionsConfig(
            python_plugins=[
                PythonPlugin(module="src.tools.custom"),
            ]
        )
        assert len(config.python_plugins) == 1

    def test_empty_defaults(self) -> None:
        """Should default to empty lists."""
        config = ExtensionsConfig()
        assert config.python_plugins == []
        assert config.mcp_servers == []

    def test_rejects_duplicate_plugin_modules(self) -> None:
        """Should reject duplicate plugin modules."""
        with pytest.raises(ValidationError, match="Duplicate plugin modules"):
            ExtensionsConfig(
                python_plugins=[
                    PythonPlugin(module="src.tools.custom"),
                    PythonPlugin(module="src.tools.custom"),
                ]
            )

    def test_rejects_duplicate_server_names(self) -> None:
        """Should reject duplicate MCP server names."""
        with pytest.raises(ValidationError, match="Duplicate MCP server names"):
            ExtensionsConfig(
                mcp_servers=[
                    McpServer(name="server1", command=["uvx", "test"]),
                    McpServer(name="server1", url="https://example.com/mcp"),
                ]
            )

    def test_allows_unique_extensions(self) -> None:
        """Should allow unique plugins and servers."""
        config = ExtensionsConfig(
            python_plugins=[
                PythonPlugin(module="src.tools.one"),
                PythonPlugin(module="src.tools.two"),
            ],
            mcp_servers=[
                McpServer(name="server1", command=["uvx", "test1"]),
                McpServer(name="server2", command=["uvx", "test2"]),
            ],
        )
        assert len(config.python_plugins) == 2
        assert len(config.mcp_servers) == 2


class TestBudgetConfig:
    """Tests for BudgetConfig model."""

    def test_valid_budget_config(self) -> None:
        """Should create BudgetConfig with valid inputs."""
        config = BudgetConfig(
            daily_limit_usd=5.0,
            monthly_limit_usd=50.0,
            alert_threshold_pct=80.0,
        )
        assert config.daily_limit_usd == 5.0
        assert config.monthly_limit_usd == 50.0
        assert config.alert_threshold_pct == 80.0

    def test_default_alert_threshold(self) -> None:
        """Should default alert_threshold_pct to 80.0."""
        config = BudgetConfig(daily_limit_usd=5.0, monthly_limit_usd=50.0)
        assert config.alert_threshold_pct == 80.0

    def test_rejects_zero_daily_limit(self) -> None:
        """Should reject zero daily limit."""
        with pytest.raises(ValidationError):
            BudgetConfig(daily_limit_usd=0.0, monthly_limit_usd=50.0)

    def test_rejects_negative_daily_limit(self) -> None:
        """Should reject negative daily limit."""
        with pytest.raises(ValidationError):
            BudgetConfig(daily_limit_usd=-1.0, monthly_limit_usd=50.0)

    def test_rejects_zero_monthly_limit(self) -> None:
        """Should reject zero monthly limit."""
        with pytest.raises(ValidationError):
            BudgetConfig(daily_limit_usd=5.0, monthly_limit_usd=0.0)

    def test_rejects_negative_threshold(self) -> None:
        """Should reject negative alert threshold."""
        with pytest.raises(ValidationError):
            BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=-1.0,
            )

    def test_rejects_threshold_over_100(self) -> None:
        """Should reject alert threshold over 100."""
        with pytest.raises(ValidationError):
            BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                alert_threshold_pct=101.0,
            )

    def test_rejects_extra_fields(self) -> None:
        """Should reject extra fields (strict schema)."""
        with pytest.raises(ValidationError):
            BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
                unknown_field="value",  # type: ignore
            )

    def test_rejects_daily_exceeding_monthly(self) -> None:
        """Should reject daily limit greater than monthly limit."""
        with pytest.raises(ValidationError, match="cannot exceed"):
            BudgetConfig(daily_limit_usd=100.0, monthly_limit_usd=50.0)

    def test_accepts_equal_daily_and_monthly(self) -> None:
        """Should accept daily limit equal to monthly limit."""
        config = BudgetConfig(daily_limit_usd=50.0, monthly_limit_usd=50.0)
        assert config.daily_limit_usd == config.monthly_limit_usd


class TestWidgetConfig:
    """Tests for WidgetConfig model."""

    def test_defaults(self) -> None:
        """Should have all-None/empty defaults."""
        widget = WidgetConfig()
        assert widget.title is None
        assert widget.initial_message is None
        assert widget.placeholder is None
        assert widget.suggested_questions == []

    def test_full_config(self) -> None:
        """Should accept all fields."""
        widget = WidgetConfig(
            title="HED Assistant",
            initial_message="Hi! I'm the HED Assistant.",
            placeholder="Ask about HED...",
            suggested_questions=[
                "What is HED?",
                "How do I annotate events?",
            ],
        )
        assert widget.title == "HED Assistant"
        assert widget.initial_message == "Hi! I'm the HED Assistant."
        assert widget.placeholder == "Ask about HED..."
        assert len(widget.suggested_questions) == 2

    def test_rejects_extra_fields(self) -> None:
        """Should reject unknown fields (extra='forbid')."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            WidgetConfig(title="Test", unknown_field="bad")

    def test_empty_questions_list(self) -> None:
        """Should accept an empty suggested_questions list."""
        widget = WidgetConfig(suggested_questions=[])
        assert widget.suggested_questions == []

    def test_empty_string_normalized_to_none(self) -> None:
        """Empty strings should be normalized to None."""
        widget = WidgetConfig(title="", placeholder="  ", initial_message="  \n  ")
        assert widget.title is None
        assert widget.placeholder is None
        assert widget.initial_message is None

    def test_strings_are_stripped(self) -> None:
        """Whitespace should be stripped from string fields."""
        widget = WidgetConfig(title="  HED Assistant  ", placeholder="  Ask... ")
        assert widget.title == "HED Assistant"
        assert widget.placeholder == "Ask..."

    def test_title_max_length(self) -> None:
        """Should enforce title max length."""
        with pytest.raises(ValidationError):
            WidgetConfig(title="x" * 101)

    def test_initial_message_max_length(self) -> None:
        """Should enforce initial_message max length."""
        with pytest.raises(ValidationError):
            WidgetConfig(initial_message="x" * 1001)

    def test_theme_color_valid(self) -> None:
        """Should accept valid hex color codes."""
        widget = WidgetConfig(theme_color="#008a79")
        assert widget.theme_color == "#008a79"

    def test_theme_color_rejects_invalid_format(self) -> None:
        """Should reject non-hex color values."""
        with pytest.raises(ValidationError):
            WidgetConfig(theme_color="red")
        with pytest.raises(ValidationError):
            WidgetConfig(theme_color="008a79")
        with pytest.raises(ValidationError):
            WidgetConfig(theme_color="#abc")

    def test_theme_color_defaults_to_none(self) -> None:
        """Should default to None when not specified."""
        widget = WidgetConfig()
        assert widget.theme_color is None

    def test_resolve_includes_theme_color_when_set(self) -> None:
        """resolve() should include theme_color when specified."""
        widget = WidgetConfig(theme_color="#008a79")
        result = widget.resolve("Test")
        assert result["theme_color"] == "#008a79"

    def test_resolve_excludes_theme_color_when_none(self) -> None:
        """resolve() should not include theme_color when None."""
        widget = WidgetConfig()
        result = widget.resolve("Test")
        assert "theme_color" not in result

    def test_user_bubble_color_valid(self) -> None:
        """Should accept a valid hex color for the reader's bubbles."""
        widget = WidgetConfig(user_bubble_color="#257a92")
        assert widget.user_bubble_color == "#257a92"

    def test_user_bubble_color_rejects_invalid_format(self) -> None:
        """Should reject the same non-hex values theme_color does."""
        for bad in ("teal", "257a92", "#abc", "#257a92;background:red"):
            with pytest.raises(ValidationError):
                WidgetConfig(user_bubble_color=bad)

    def test_theme_color_alone_sets_no_bubble_color(self) -> None:
        """A community that sets only theme_color keeps the platform bubbles: resolve()
        adds no user_bubble_color, so the widget never derives one from the theme."""
        result = WidgetConfig(theme_color="#008a79").resolve("Test")
        assert result["theme_color"] == "#008a79"
        assert "user_bubble_color" not in result

    def test_resolve_includes_user_bubble_color_when_set(self) -> None:
        """resolve() should include user_bubble_color when specified."""
        result = WidgetConfig(user_bubble_color="#257a92").resolve("Test")
        assert result["user_bubble_color"] == "#257a92"

    def test_theme_text_color_valid(self) -> None:
        """Should accept a valid hex color for text drawn on theme_color surfaces."""
        widget = WidgetConfig(theme_text_color="#04121f")
        assert widget.theme_text_color == "#04121f"

    def test_theme_text_color_rejects_invalid_format(self) -> None:
        """Should reject the same non-hex values theme_color does."""
        for bad in ("navy", "04121f", "#abc", "#04121f;background:red"):
            with pytest.raises(ValidationError):
                WidgetConfig(theme_text_color=bad)

    def test_theme_text_color_defaults_to_none(self) -> None:
        """Should default to None when not specified."""
        widget = WidgetConfig()
        assert widget.theme_text_color is None

    def test_theme_color_alone_sets_no_theme_text_color(self) -> None:
        """A community that sets only theme_color keeps the widget's white on-primary
        text: resolve() adds no theme_text_color, so the widget never derives one."""
        result = WidgetConfig(theme_color="#008a79").resolve("Test")
        assert result["theme_color"] == "#008a79"
        assert "theme_text_color" not in result

    def test_resolve_includes_theme_text_color_when_set(self) -> None:
        """resolve() should include theme_text_color when specified."""
        result = WidgetConfig(theme_text_color="#04121f").resolve("Test")
        assert result["theme_text_color"] == "#04121f"

    def test_accent_color_valid(self) -> None:
        """Should accept a valid hex color for theme_color used as a foreground."""
        widget = WidgetConfig(accent_color="#257a92")
        assert widget.accent_color == "#257a92"

    def test_accent_color_rejects_invalid_format(self) -> None:
        """Should reject the same non-hex values theme_color does."""
        for bad in ("teal", "257a92", "#abc", "#257a92;background:red"):
            with pytest.raises(ValidationError):
                WidgetConfig(accent_color=bad)

    def test_accent_color_defaults_to_none(self) -> None:
        """Should default to None when not specified."""
        widget = WidgetConfig()
        assert widget.accent_color is None

    def test_theme_color_alone_sets_no_accent_color(self) -> None:
        """A community that sets only theme_color keeps the widget's own default, which
        tracks theme_color directly in the stylesheet: resolve() adds no accent_color."""
        result = WidgetConfig(theme_color="#008a79").resolve("Test")
        assert result["theme_color"] == "#008a79"
        assert "accent_color" not in result

    def test_resolve_includes_accent_color_when_set(self) -> None:
        """resolve() should include accent_color when specified."""
        result = WidgetConfig(accent_color="#257a92").resolve("Test")
        assert result["accent_color"] == "#257a92"

    def test_user_bubble_text_color_valid(self) -> None:
        """Should accept a valid hex color for text in the reader's own bubbles."""
        widget = WidgetConfig(user_bubble_text_color="#04121f")
        assert widget.user_bubble_text_color == "#04121f"

    def test_user_bubble_text_color_rejects_invalid_format(self) -> None:
        """Should reject the same non-hex values user_bubble_color does."""
        for bad in ("navy", "04121f", "#abc", "#04121f;background:red"):
            with pytest.raises(ValidationError):
                WidgetConfig(user_bubble_text_color=bad)

    def test_user_bubble_text_color_defaults_to_none(self) -> None:
        """Should default to None when not specified."""
        widget = WidgetConfig()
        assert widget.user_bubble_text_color is None

    def test_user_bubble_color_alone_sets_no_text_color(self) -> None:
        """A community that sets only user_bubble_color keeps the widget's white
        bubble text: resolve() adds no user_bubble_text_color."""
        result = WidgetConfig(user_bubble_color="#5bbad5").resolve("Test")
        assert result["user_bubble_color"] == "#5bbad5"
        assert "user_bubble_text_color" not in result

    def test_resolve_includes_user_bubble_text_color_when_set(self) -> None:
        """resolve() should include user_bubble_text_color when specified."""
        result = WidgetConfig(user_bubble_text_color="#04121f").resolve("Test")
        assert result["user_bubble_text_color"] == "#04121f"

    def test_launcher_defaults_to_bubble(self) -> None:
        """Should default to 'bubble', today's single-icon launcher."""
        widget = WidgetConfig()
        assert widget.launcher == "bubble"

    def test_launcher_accepts_capsule(self) -> None:
        """Should accept the three-icon capsule launcher (#436)."""
        widget = WidgetConfig(launcher="capsule")
        assert widget.launcher == "capsule"

    def test_launcher_rejects_invalid_value(self) -> None:
        """Should reject any value other than 'bubble' or 'capsule'."""
        with pytest.raises(ValidationError):
            WidgetConfig(launcher="pill")

    def test_resolve_omits_launcher_when_bubble(self) -> None:
        """resolve() should omit launcher for the 'bubble' default, so a community that
        never sets it renders exactly as it did before this field existed."""
        result = WidgetConfig().resolve("Test")
        assert "launcher" not in result

    def test_resolve_includes_launcher_when_capsule(self) -> None:
        """resolve() should include launcher when it is 'capsule'."""
        result = WidgetConfig(launcher="capsule").resolve("Test")
        assert result["launcher"] == "capsule"

    def test_color_scheme_defaults_to_light_and_is_omitted(self) -> None:
        """A community that never sets color_scheme resolves exactly as before it existed."""
        widget = WidgetConfig()
        assert widget.color_scheme == "light"
        assert "color_scheme" not in widget.resolve("Test")

    def test_color_scheme_auto_is_included(self) -> None:
        result = WidgetConfig(color_scheme="auto").resolve("Test")
        assert result["color_scheme"] == "auto"

    @pytest.mark.parametrize("value", ["dark", "Auto", "system", ""])
    def test_color_scheme_rejects_anything_but_light_or_auto(self, value: str) -> None:
        """'dark' is refused on purpose: a forced-dark widget on a light page is a host
        page's choice (setColorScheme), not a community default."""
        with pytest.raises(ValidationError):
            WidgetConfig(color_scheme=value)

    @pytest.mark.parametrize("value", ["dark", "light", "system"])
    def test_the_widget_response_can_only_carry_auto(self, value: str) -> None:
        """The response model is its own guard, whichever code path builds it: 'light'
        is omitted rather than sent, and 'dark' is never a community's to send."""
        from src.api.routers.community import WidgetConfigResponse

        # Built from resolve(), the way the config route builds it.
        auto = WidgetConfigResponse(**WidgetConfig(color_scheme="auto").resolve("T"))
        assert auto.color_scheme == "auto"
        light = WidgetConfig().resolve("T")
        assert WidgetConfigResponse(**light).color_scheme is None
        with pytest.raises(ValidationError):
            WidgetConfigResponse(**{**light, "color_scheme": value})

    def test_launcher_label_valid(self) -> None:
        """Should accept a short plain-text tooltip label."""
        widget = WidgetConfig(launcher_label="Explore NEMAR")
        assert widget.launcher_label == "Explore NEMAR"

    def test_launcher_label_strips_whitespace(self) -> None:
        """Should strip surrounding whitespace."""
        widget = WidgetConfig(launcher_label="  Explore NEMAR  ")
        assert widget.launcher_label == "Explore NEMAR"

    def test_launcher_label_empty_normalizes_to_none(self) -> None:
        """A whitespace-only label should normalize to None, like other text fields."""
        widget = WidgetConfig(launcher_label="   ")
        assert widget.launcher_label is None

    def test_launcher_label_rejects_markup(self) -> None:
        """Should reject values containing '<' or '>' as not plain text."""
        with pytest.raises(ValidationError):
            WidgetConfig(launcher_label="<b>Explore</b>")

    def test_launcher_label_max_length(self) -> None:
        """Should enforce the 40-character maximum."""
        with pytest.raises(ValidationError):
            WidgetConfig(launcher_label="x" * 41)
        widget = WidgetConfig(launcher_label="x" * 40)
        assert widget.launcher_label == "x" * 40

    def test_launcher_label_defaults_to_none(self) -> None:
        """Should default to None, keeping the widget's hardcoded tooltip text."""
        widget = WidgetConfig()
        assert widget.launcher_label is None

    def test_resolve_omits_launcher_label_when_unset(self) -> None:
        """resolve() should omit launcher_label when not specified."""
        result = WidgetConfig().resolve("Test")
        assert "launcher_label" not in result

    def test_resolve_includes_launcher_label_when_set(self) -> None:
        """resolve() should include launcher_label when specified."""
        result = WidgetConfig(launcher_label="Explore NEMAR").resolve("Test")
        assert result["launcher_label"] == "Explore NEMAR"

    def test_placeholder_max_length(self) -> None:
        """Should enforce placeholder max length."""
        with pytest.raises(ValidationError):
            WidgetConfig(placeholder="x" * 201)

    def test_suggested_questions_filters_empty(self) -> None:
        """Should filter out empty and whitespace-only questions."""
        widget = WidgetConfig(suggested_questions=["What is HED?", "", "  ", "How?"])
        assert widget.suggested_questions == ["What is HED?", "How?"]

    def test_suggested_questions_strips_whitespace(self) -> None:
        """Should strip whitespace from question entries."""
        widget = WidgetConfig(suggested_questions=["  What is HED?  "])
        assert widget.suggested_questions == ["What is HED?"]

    def test_suggested_questions_max_count(self) -> None:
        """Should reject more than 10 suggested questions."""
        with pytest.raises(ValidationError, match="Maximum is 10"):
            WidgetConfig(suggested_questions=[f"Question {i}" for i in range(11)])

    def test_is_frozen(self) -> None:
        """Should be immutable after construction."""
        widget = WidgetConfig(title="Test")
        with pytest.raises(ValidationError):
            widget.title = "Changed"

    def test_resolve_with_defaults(self) -> None:
        """resolve() should apply defaults from community name."""
        widget = WidgetConfig()
        result = widget.resolve("HED")
        assert result["title"] == "HED"
        assert result["placeholder"] == "Ask a question..."
        assert result["initial_message"] is None
        assert result["suggested_questions"] == []

    def test_resolve_with_values(self) -> None:
        """resolve() should use provided values over defaults."""
        widget = WidgetConfig(
            title="Custom Title",
            placeholder="Custom placeholder",
        )
        result = widget.resolve("HED")
        assert result["title"] == "Custom Title"
        assert result["placeholder"] == "Custom placeholder"


class TestWidgetConfigLogoUrl:
    """Tests for WidgetConfig.logo_url field and validation."""

    def test_logo_url_accepts_https(self) -> None:
        """Should accept HTTPS URLs."""
        widget = WidgetConfig(logo_url="https://example.com/logo.png")
        assert widget.logo_url == "https://example.com/logo.png"

    def test_logo_url_accepts_http(self) -> None:
        """Should accept HTTP URLs."""
        widget = WidgetConfig(logo_url="http://example.com/logo.png")
        assert widget.logo_url == "http://example.com/logo.png"

    def test_logo_url_accepts_relative_path(self) -> None:
        """Should accept paths starting with /."""
        widget = WidgetConfig(logo_url="/hed/logo")
        assert widget.logo_url == "/hed/logo"

    def test_logo_url_rejects_javascript(self) -> None:
        """Should reject javascript: URLs."""
        with pytest.raises(ValidationError, match="logo_url must use"):
            WidgetConfig(logo_url="javascript:alert(1)")

    def test_logo_url_rejects_data_uri(self) -> None:
        """Should reject data: URIs."""
        with pytest.raises(ValidationError, match="logo_url must use"):
            WidgetConfig(logo_url="data:text/html,<script>alert(1)</script>")

    def test_logo_url_rejects_ftp(self) -> None:
        """Should reject ftp: URLs."""
        with pytest.raises(ValidationError, match="logo_url must use"):
            WidgetConfig(logo_url="ftp://example.com/logo.png")

    def test_logo_url_none_by_default(self) -> None:
        """Should default to None."""
        widget = WidgetConfig()
        assert widget.logo_url is None

    def test_logo_url_empty_string_normalized(self) -> None:
        """Empty or whitespace-only string should become None."""
        widget = WidgetConfig(logo_url="   ")
        assert widget.logo_url is None

    def test_logo_url_strips_whitespace(self) -> None:
        """Should strip whitespace from logo_url."""
        widget = WidgetConfig(logo_url="  https://example.com/logo.png  ")
        assert widget.logo_url == "https://example.com/logo.png"

    def test_resolve_with_logo_url_fallback(self) -> None:
        """resolve() should use fallback logo_url when self.logo_url is None."""
        widget = WidgetConfig()
        result = widget.resolve("Test", logo_url="/test/logo")
        assert result["logo_url"] == "/test/logo"

    def test_resolve_explicit_logo_url_takes_precedence(self) -> None:
        """resolve() should prefer explicit logo_url over fallback."""
        widget = WidgetConfig(logo_url="https://example.com/explicit.png")
        result = widget.resolve("Test", logo_url="/test/logo")
        assert result["logo_url"] == "https://example.com/explicit.png"

    def test_resolve_no_logo_url_returns_none(self) -> None:
        """resolve() should return None when no logo_url is set anywhere."""
        widget = WidgetConfig()
        result = widget.resolve("Test")
        assert result["logo_url"] is None


class TestCommunityConfigWidget:
    """Tests for CommunityConfig.widget field."""

    def test_widget_optional(self) -> None:
        """Widget field should be optional and default to None."""
        config = CommunityConfig(
            id="test",
            name="Test Community",
            description="A test",
        )
        assert config.widget is None

    def test_widget_in_config(self) -> None:
        """Should accept widget config in CommunityConfig."""
        config = CommunityConfig(
            id="test",
            name="Test Community",
            description="A test",
            widget=WidgetConfig(
                title="Test Assistant",
                placeholder="Ask...",
                suggested_questions=["What is this?"],
            ),
        )
        assert config.widget is not None
        assert config.widget.title == "Test Assistant"
        assert len(config.widget.suggested_questions) == 1


class TestCommunityConfigBudget:
    """Tests for CommunityConfig.budget field."""

    def test_budget_none_by_default(self) -> None:
        """Should default budget to None."""
        config = CommunityConfig(id="test", name="Test", description="Test")
        assert config.budget is None

    def test_budget_config_set(self) -> None:
        """Should accept valid budget config."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            budget=BudgetConfig(
                daily_limit_usd=5.0,
                monthly_limit_usd=50.0,
            ),
        )
        assert config.budget is not None
        assert config.budget.daily_limit_usd == 5.0

    def test_budget_from_yaml_dict(self) -> None:
        """Should parse budget from YAML-like dict."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            budget={
                "daily_limit_usd": 10.0,
                "monthly_limit_usd": 100.0,
                "alert_threshold_pct": 90.0,
            },
        )
        assert config.budget.daily_limit_usd == 10.0
        assert config.budget.alert_threshold_pct == 90.0


class TestCommunityConfig:
    """Tests for CommunityConfig model."""

    def test_valid_community(self) -> None:
        """Should create CommunityConfig with valid inputs."""
        config = CommunityConfig(
            id="test",
            name="Test Community",
            description="A test community",
        )
        assert config.id == "test"
        assert config.name == "Test Community"
        assert config.status == "available"

    def test_full_community_config(self) -> None:
        """Should create CommunityConfig with all fields."""
        config = CommunityConfig(
            id="hed",
            name="HED",
            description="HED annotation",
            status="available",
            documentation=[
                DocSource(
                    title="HED Resources",
                    url="https://hedtags.org/hed-resources/",
                    type="sphinx",
                ),
            ],
            github=GitHubConfig(repos=["hed-standard/hed-python"]),
            citations=CitationConfig(
                queries=["HED annotation"],
                dois=["10.1234/hed"],
            ),
            extensions=ExtensionsConfig(
                python_plugins=[
                    PythonPlugin(module="src.assistants.hed.tools"),
                ]
            ),
        )
        assert len(config.documentation) == 1
        assert config.github is not None
        assert len(config.github.repos) == 1
        assert config.citations is not None
        assert len(config.citations.queries) == 1
        assert config.enable_page_context is True  # Default value

    def test_get_sync_config(self) -> None:
        """Should generate sync_config dict from community config."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            github=GitHubConfig(repos=["org/repo"]),
            citations=CitationConfig(
                queries=["query"],
                dois=["10.1234/doi"],
            ),
        )
        sync = config.get_sync_config()
        assert sync["github_repos"] == ["org/repo"]
        assert sync["paper_queries"] == ["query"]
        assert sync["paper_dois"] == ["10.1234/doi"]

    def test_get_sync_config_empty(self) -> None:
        """Should return empty dict when no sync-related config."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
        )
        sync = config.get_sync_config()
        assert sync == {}

    def test_invalid_status_raises_error(self) -> None:
        """Should reject invalid status values."""
        with pytest.raises(ValidationError):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                status="invalid",  # type: ignore
            )

    def test_validates_kebab_case_id(self) -> None:
        """Should validate ID is kebab-case."""
        # Valid kebab-case IDs
        valid_ids = ["hed", "bids-validator", "eeglab-2024", "my-tool"]
        for id_val in valid_ids:
            config = CommunityConfig(id=id_val, name="Test", description="Test")
            assert config.id == id_val

    def test_rejects_invalid_id_format(self) -> None:
        """Should reject non-kebab-case IDs."""
        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="InvalidCase", name="Test", description="Test")

        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="with spaces", name="Test", description="Test")

        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="with_underscores", name="Test", description="Test")

        with pytest.raises(ValidationError, match="kebab-case"):
            CommunityConfig(id="with.dots", name="Test", description="Test")

    def test_rejects_empty_id(self) -> None:
        """Should reject empty ID."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            CommunityConfig(id="", name="Test", description="Test")

        with pytest.raises(ValidationError, match="cannot be empty"):
            CommunityConfig(id="  ", name="Test", description="Test")


class TestCommunityConfigCorsOrigins:
    """Tests for CommunityConfig.cors_origins validation."""

    def test_valid_exact_origins(self) -> None:
        """Should accept valid exact origin URLs."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://example.org",
                "https://www.hedtags.org",
                "http://localhost:3000",
                "https://my-site.example.com:8080",
            ],
        )
        assert len(config.cors_origins) == 4

    def test_valid_wildcard_origins(self) -> None:
        """Should accept valid wildcard subdomain patterns."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://*.pages.dev",
                "https://*.osa-demo.pages.dev",
                "http://*.localhost:3000",
            ],
        )
        assert len(config.cors_origins) == 3

    def test_defaults_to_empty(self) -> None:
        """Should default to empty list."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
        )
        assert config.cors_origins == []

    def test_rejects_origin_without_scheme(self) -> None:
        """Should reject origins missing http/https scheme."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["example.org"],
            )

    def test_rejects_origin_with_path(self) -> None:
        """Should reject origins with paths."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["https://example.org/path"],
            )

    def test_rejects_invalid_wildcard_position(self) -> None:
        """Should reject wildcards not at subdomain position."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["https://example.*.com"],
            )

    def test_deduplicates_origins(self) -> None:
        """Should deduplicate origin entries."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://example.org",
                "https://example.org",
                "https://other.org",
            ],
        )
        assert len(config.cors_origins) == 2

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace from origins."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=["  https://example.org  "],
        )
        assert config.cors_origins == ["https://example.org"]

    def test_skips_empty_strings(self) -> None:
        """Should skip empty strings."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=["", "  ", "https://example.org"],
        )
        assert config.cors_origins == ["https://example.org"]

    def test_rejects_too_long_origin(self) -> None:
        """Should reject origins longer than 255 characters."""
        long_origin = "https://" + "a" * 248
        with pytest.raises(ValidationError, match="too long"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=[long_origin],
            )

    def test_accepts_single_char_subdomain_labels(self) -> None:
        """Should accept origins with single-character subdomain labels."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=[
                "https://a.example.org",
                "https://1.example.org",
            ],
        )
        assert len(config.cors_origins) == 2

    def test_rejects_leading_hyphen_in_domain(self) -> None:
        """Should reject origins with leading hyphens in domain labels."""
        with pytest.raises(ValidationError, match="Invalid CORS origin"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                cors_origins=["https://-example.org"],
            )

    def test_accepts_numeric_only_domain(self) -> None:
        """Should accept origins with numeric-only domain labels."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            cors_origins=["https://123.456.789:8080"],
        )
        assert len(config.cors_origins) == 1


class TestCommunityConfigMaintainers:
    """Tests for CommunityConfig.maintainers validation."""

    def test_valid_maintainers(self) -> None:
        """Should accept valid GitHub usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["octocat", "jane-doe", "user123"],
        )
        assert config.maintainers == ["octocat", "jane-doe", "user123"]

    def test_defaults_to_empty(self) -> None:
        """Should default to empty list."""
        config = CommunityConfig(id="test", name="Test", description="Test")
        assert config.maintainers == []

    def test_rejects_invalid_username_with_special_chars(self) -> None:
        """Should reject usernames with special characters."""
        with pytest.raises(ValidationError, match="Invalid GitHub username"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                maintainers=["bad@user"],
            )

    def test_rejects_username_starting_with_hyphen(self) -> None:
        """Should reject usernames starting with hyphen."""
        with pytest.raises(ValidationError, match="Invalid GitHub username"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                maintainers=["-badstart"],
            )

    def test_rejects_username_ending_with_hyphen(self) -> None:
        """Should reject usernames ending with hyphen."""
        with pytest.raises(ValidationError, match="Invalid GitHub username"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                maintainers=["badend-"],
            )

    def test_deduplicates_maintainers(self) -> None:
        """Should remove duplicate usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["octocat", "octocat", "jane"],
        )
        assert config.maintainers == ["octocat", "jane"]

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace from usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["  octocat  ", " jane "],
        )
        assert config.maintainers == ["octocat", "jane"]

    def test_single_char_username(self) -> None:
        """Should accept single-character usernames."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            maintainers=["a"],
        )
        assert config.maintainers == ["a"]


class TestCommunitiesConfig:
    """Tests for CommunitiesConfig model."""

    def test_empty_communities(self) -> None:
        """Should allow empty communities list."""
        config = CommunitiesConfig()
        assert config.communities == []

    def test_get_community(self) -> None:
        """Should find community by ID."""
        config = CommunitiesConfig(
            communities=[
                CommunityConfig(id="one", name="One", description="First"),
                CommunityConfig(id="two", name="Two", description="Second"),
            ]
        )
        assert config.get_community("one") is not None
        assert config.get_community("one").name == "One"
        assert config.get_community("nonexistent") is None

    def test_rejects_duplicate_community_ids(self) -> None:
        """Should reject duplicate community IDs."""
        with pytest.raises(ValidationError, match="Duplicate community IDs"):
            CommunitiesConfig(
                communities=[
                    CommunityConfig(id="test", name="Test 1", description="First"),
                    CommunityConfig(id="test", name="Test 2", description="Second"),
                ]
            )

    def test_allows_unique_community_ids(self) -> None:
        """Should allow unique community IDs."""
        config = CommunitiesConfig(
            communities=[
                CommunityConfig(id="one", name="One", description="First"),
                CommunityConfig(id="two", name="Two", description="Second"),
                CommunityConfig(id="three", name="Three", description="Third"),
            ]
        )
        assert len(config.communities) == 3

    def test_from_yaml(self) -> None:
        """Should load configuration from YAML file."""
        yaml_content = """
communities:
  - id: test
    name: Test Community
    description: A test
    status: available
    github:
      repos:
        - org/repo
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            config = CommunitiesConfig.from_yaml(yaml_path)
            assert len(config.communities) == 1
            assert config.communities[0].id == "test"
            assert config.communities[0].github is not None
            assert config.communities[0].github.repos == ["org/repo"]
        finally:
            yaml_path.unlink()

    def test_from_yaml_empty_file(self) -> None:
        """Should handle empty YAML file."""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            yaml_path = Path(f.name)

        try:
            config = CommunitiesConfig.from_yaml(yaml_path)
            assert config.communities == []
        finally:
            yaml_path.unlink()

    def test_from_yaml_missing_file(self) -> None:
        """Should raise error for missing file."""
        with pytest.raises(FileNotFoundError):
            CommunitiesConfig.from_yaml(Path("/nonexistent/path.yaml"))

    def test_from_yaml_invalid_structure(self) -> None:
        """Should raise validation error for invalid YAML structure."""
        yaml_content = """
communities:
  - id: 123  # Should be string
    name: Test
    description: Test
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            # Pydantic does NOT coerce integers to strings in strict mode
            with pytest.raises(ValidationError):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_extra_fields_rejected(self) -> None:
        """Should reject extra fields (strict schema)."""
        yaml_content = """
communities:
  - id: test
    name: Test
    description: Test
    unknown_field: value
"""
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            with pytest.raises(ValidationError):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_from_yaml_malformed_syntax(self) -> None:
        """Should raise YAMLError for malformed YAML syntax."""
        import yaml

        yaml_content = "bad: yaml: syntax: error:"
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            with pytest.raises(yaml.YAMLError, match="Failed to parse YAML"):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_from_yaml_null_communities(self) -> None:
        """Should reject YAML with null communities field."""
        yaml_content = "communities: null"
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            yaml_path = Path(f.name)

        try:
            with pytest.raises(ValidationError, match="should be a valid list"):
                CommunitiesConfig.from_yaml(yaml_path)
        finally:
            yaml_path.unlink()

    def test_load_actual_communities_yaml(self) -> None:
        """Should successfully load the real communities.yaml from the repo."""
        project_root = Path(__file__).parent.parent.parent.parent
        yaml_path = project_root / "registries" / "communities.yaml"

        # Skip if YAML doesn't exist (e.g., in isolated test environments)
        if not yaml_path.exists():
            pytest.skip(f"communities.yaml not found at {yaml_path}")

        config = CommunitiesConfig.from_yaml(yaml_path)

        # Basic validation
        assert len(config.communities) > 0, "Should have at least one community"

        for community in config.communities:
            # Validate structure
            assert community.id, "Community missing id"
            assert community.name, f"Community {community.id} missing name"
            assert community.description, f"Community {community.id} missing description"

            # If documentation URLs are provided, they should be valid
            for doc in community.documentation:
                assert doc.url, f"Doc in {community.id} missing URL"


class TestEnvVarNameValidation:
    """Tests for openrouter_api_key_env_var validation (Issue #64)."""

    def test_valid_env_var_names(self) -> None:
        """Should accept valid OPENROUTER_API_KEY_* patterns."""
        valid_names = [
            "OPENROUTER_API_KEY_HED",
            "OPENROUTER_API_KEY_BIDS",
            "OPENROUTER_API_KEY_TEST",
            "OPENROUTER_API_KEY_MY_COMMUNITY",
            "OPENROUTER_API_KEY_123",
        ]
        for name in valid_names:
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                openrouter_api_key_env_var=name,
            )
            assert config.openrouter_api_key_env_var == name

    def test_allows_none(self) -> None:
        """Should allow None (use platform key)."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            openrouter_api_key_env_var=None,
        )
        assert config.openrouter_api_key_env_var is None

    def test_rejects_arbitrary_env_vars(self) -> None:
        """Should reject non-OPENROUTER_API_KEY_* patterns (prevents secret access)."""
        invalid_names = [
            "AWS_SECRET_KEY",
            "DATABASE_PASSWORD",
            "SOME_OTHER_SECRET",
            "OPENROUTER_KEY",  # Missing API_KEY part
            "API_KEY_HED",  # Missing OPENROUTER part
            "openrouter_api_key_hed",  # Lowercase not allowed
        ]
        for name in invalid_names:
            with pytest.raises(ValidationError, match="Invalid environment variable name"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    openrouter_api_key_env_var=name,
                )

    def test_rejects_env_var_with_special_chars(self) -> None:
        """Should reject env var names with special characters."""
        with pytest.raises(ValidationError, match="Invalid environment variable name"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                openrouter_api_key_env_var="OPENROUTER_API_KEY-HED",  # Hyphen not allowed
            )

    def test_strips_whitespace_from_env_var(self) -> None:
        """Should strip whitespace from env var names."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            openrouter_api_key_env_var="  OPENROUTER_API_KEY_HED  ",
        )
        assert config.openrouter_api_key_env_var == "OPENROUTER_API_KEY_HED"


class TestAnthropicEnvVarNameValidation:
    """Tests for anthropic_api_key_env_var validation (Phase 2, issue #362)."""

    def test_valid_env_var_names(self) -> None:
        """Should accept valid ANTHROPIC_API_KEY_* patterns."""
        valid_names = [
            "ANTHROPIC_API_KEY_HED",
            "ANTHROPIC_API_KEY_BIDS",
            "ANTHROPIC_API_KEY_TEST",
            "ANTHROPIC_API_KEY_MY_COMMUNITY",
            "ANTHROPIC_API_KEY_123",
        ]
        for name in valid_names:
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                anthropic_api_key_env_var=name,
            )
            assert config.anthropic_api_key_env_var == name

    def test_allows_none(self) -> None:
        """Should allow None (use platform key)."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            anthropic_api_key_env_var=None,
        )
        assert config.anthropic_api_key_env_var is None

    def test_rejects_arbitrary_env_vars(self) -> None:
        """Should reject non-ANTHROPIC_API_KEY_* patterns (prevents secret access)."""
        invalid_names = [
            "AWS_SECRET_KEY",
            "DATABASE_PASSWORD",
            "ANTHROPIC_KEY",  # Missing API_KEY part
            "API_KEY_HED",  # Missing ANTHROPIC part
            "anthropic_api_key_hed",  # Lowercase not allowed
            "OPENROUTER_API_KEY_HED",  # Wrong provider prefix
        ]
        for name in invalid_names:
            with pytest.raises(ValidationError, match="Invalid environment variable name"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    anthropic_api_key_env_var=name,
                )

    def test_strips_whitespace_from_env_var(self) -> None:
        """Should strip whitespace from env var names."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            anthropic_api_key_env_var="  ANTHROPIC_API_KEY_HED  ",
        )
        assert config.anthropic_api_key_env_var == "ANTHROPIC_API_KEY_HED"

    def test_both_env_vars_can_coexist(self) -> None:
        """A community may configure both provider env vars simultaneously."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            anthropic_api_key_env_var="ANTHROPIC_API_KEY_TEST",
            openrouter_api_key_env_var="OPENROUTER_API_KEY_TEST",
        )
        assert config.anthropic_api_key_env_var == "ANTHROPIC_API_KEY_TEST"
        assert config.openrouter_api_key_env_var == "OPENROUTER_API_KEY_TEST"


class TestNoShippedOpenRouterKeyEnvVar:
    """No shipped community claims a per-community OpenRouter key (Phase 3).

    The named env vars were never set on the server, so every request for
    those communities logged an ERROR in ``_resolve_provider`` and silently
    fell back to the platform key. The schema field itself stays supported
    for a community that genuinely funds itself through OpenRouter; this
    just asserts nothing in the tree claims one any more. Iterates the real
    config.yaml files rather than hardcoding the four affected community
    ids, per .rules/testing_guidelines.md.
    """

    def test_no_shipped_config_sets_openrouter_api_key_env_var(self) -> None:
        from src.assistants import discover_assistants, registry

        registry._assistants.clear()
        discover_assistants()
        communities = list(registry.list_all())
        assert communities, "Expected at least one discovered community"

        offenders = [
            assistant.id
            for assistant in communities
            if assistant.community_config
            and assistant.community_config.openrouter_api_key_env_var is not None
        ]
        assert offenders == [], (
            f"These communities still set openrouter_api_key_env_var: {offenders}"
        )

    def test_synthetic_config_with_openrouter_api_key_env_var_still_validates(self) -> None:
        """The schema field itself is still supported, just unused today."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            openrouter_api_key_env_var="OPENROUTER_API_KEY_TEST",
        )
        assert config.openrouter_api_key_env_var == "OPENROUTER_API_KEY_TEST"


class TestSSRFProtection:
    """Tests for source_url SSRF protection (Issue #66)."""

    def test_valid_public_urls(self) -> None:
        """Should accept valid public HTTP/HTTPS URLs."""
        valid_urls = [
            "https://raw.githubusercontent.com/org/repo/main/docs/file.md",
            "https://docs.example.com/content.md",
            "http://public-site.org/documentation.html",
        ]
        for url in valid_urls:
            doc = DocSource(
                title="Test Doc",
                url="https://example.com",
                source_url=url,
            )
            assert doc.source_url == url

    def test_rejects_localhost(self) -> None:
        """Should reject localhost URLs (prevents local probing)."""
        localhost_urls = [
            "http://localhost/file.md",
            "http://127.0.0.1/file.md",
            "http://[::1]/file.md",
        ]
        for url in localhost_urls:
            with pytest.raises(ValidationError, match="Cannot fetch from localhost"):
                DocSource(
                    title="Test Doc",
                    url="https://example.com",
                    source_url=url,
                )

    def test_rejects_private_ips(self) -> None:
        """Should reject private IP addresses (prevents internal network probing)."""
        private_ips = [
            "http://10.0.0.1/file.md",  # 10.0.0.0/8
            "http://172.16.0.1/file.md",  # 172.16.0.0/12
            "http://192.168.1.1/file.md",  # 192.168.0.0/16
        ]
        for url in private_ips:
            with pytest.raises(ValidationError, match="Cannot fetch from private IP"):
                DocSource(
                    title="Test Doc",
                    url="https://example.com",
                    source_url=url,
                )

    def test_rejects_aws_metadata_service(self) -> None:
        """Should reject AWS metadata service (link-local 169.254.0.0/16)."""
        with pytest.raises(ValidationError, match="link-local address"):
            DocSource(
                title="Test Doc",
                url="https://example.com",
                source_url="http://169.254.169.254/latest/meta-data/",
            )

    def test_rejects_non_http_schemes(self) -> None:
        """Should reject non-HTTP/HTTPS schemes."""
        invalid_schemes = [
            "file:///etc/passwd",
            "ftp://example.com/file.md",
            "gopher://example.com/",
            "data:text/plain,content",
        ]
        for url in invalid_schemes:
            with pytest.raises(ValidationError, match="Invalid URL scheme"):
                DocSource(
                    title="Test Doc",
                    url="https://example.com",
                    source_url=url,
                )

    def test_allows_none_source_url(self) -> None:
        """Should allow None for source_url."""
        doc = DocSource(
            title="Test Doc",
            url="https://example.com",
            source_url=None,
        )
        assert doc.source_url is None

    def test_accepts_public_hostnames(self) -> None:
        """Should accept public hostnames (not IPs)."""
        doc = DocSource(
            title="Test Doc",
            url="https://example.com",
            source_url="https://public-docs.example.org/file.md",
        )
        assert doc.source_url == "https://public-docs.example.org/file.md"


class TestModelNameValidation:
    """Tests for default_model validation (Issue #68).

    The pattern accepts both the OpenRouter creator/model-name form and a
    bare first-party id (Phase 2, issue #362): the Claude Platform on AWS
    path has no separate "creator" segment (e.g. "claude-haiku-4-5").
    """

    def test_valid_model_names(self) -> None:
        """Should accept valid provider/model-name format."""
        valid_models = [
            "anthropic/claude-3.5-sonnet",
            "openai/gpt-4",
            "google/gemini-pro",
            "provider/model-name-v2.0",
            "provider_name/model_name",
        ]
        for model in valid_models:
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=model,
            )
            assert config.default_model == model

    def test_valid_bare_first_party_ids(self) -> None:
        """Should accept a bare first-party id with no provider prefix, and
        not warn: these are real, resolvable Anthropic ids/aliases."""
        valid_bare_ids = ["claude-haiku-4-5", "claude-sonnet-5"]
        for model in valid_bare_ids:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                config = CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    default_model=model,
                )
            assert config.default_model == model

    def test_bare_id_format_valid_but_unresolvable_warns(self) -> None:
        """A bare id passes format validation (no '/') but isn't an offered
        Anthropic model or alias, so it silently falls back on the
        OpenRouter path (see validate_default_model_resolvable). The config
        still parses -- this is a warning, not an error."""
        with pytest.warns(UserWarning, match="not an offered Anthropic model"):
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model="just-a-model-name",
            )
        assert config.default_model == "just-a-model-name"

    def test_allows_none(self) -> None:
        """Should allow None (use platform default)."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            default_model=None,
        )
        assert config.default_model is None

    def test_rejects_invalid_format(self) -> None:
        """Should reject model names that are not a bare id or provider/model-name."""
        invalid_models = [
            "provider/",  # No model name
            "/model-name",  # No provider
            "provider model",  # Space instead of slash
            "provider\\model",  # Backslash
            "provider/model/extra",  # Too many slashes
        ]
        for model in invalid_models:
            with pytest.raises(ValidationError, match="Invalid model name"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    default_model=model,
                )

    def test_rejects_too_long_model_name(self) -> None:
        """Should reject model names longer than 100 characters."""
        long_model = "provider/" + "x" * 100
        with pytest.raises(ValidationError, match="too long"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=long_model,
            )

    def test_strips_whitespace(self) -> None:
        """Should strip whitespace from model names."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            default_model="  anthropic/claude-3.5-sonnet  ",
        )
        assert config.default_model == "anthropic/claude-3.5-sonnet"


class TestCostManipulationProtection:
    """Tests for cost manipulation guards (Issue #67)."""

    def test_allows_expensive_model_with_byok(self) -> None:
        """Should allow ultra-expensive models when BYOK is configured."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            default_model="anthropic/claude-opus-4",
            openrouter_api_key_env_var="OPENROUTER_API_KEY_TEST",
        )
        assert config.default_model == "anthropic/claude-opus-4"
        assert config.openrouter_api_key_env_var is not None

    def test_allows_expensive_model_with_anthropic_env_var(self) -> None:
        """Should also allow ultra-expensive models when anthropic_api_key_env_var is set."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            default_model="anthropic/claude-opus-4",
            anthropic_api_key_env_var="ANTHROPIC_API_KEY_TEST",
        )
        assert config.default_model == "anthropic/claude-opus-4"
        assert config.anthropic_api_key_env_var is not None

    def test_rejects_ultra_expensive_model_without_byok(self) -> None:
        """Should reject ultra-expensive models without BYOK (prevents surprise billing)."""
        ultra_expensive_models = [
            "openai/o1",
            "openai/o1-preview",
            "anthropic/claude-opus-4",
            "anthropic/claude-3-opus",
        ]
        for model in ultra_expensive_models:
            with pytest.raises(ValidationError, match="requires BYOK"):
                CommunityConfig(
                    id="test",
                    name="Test",
                    description="Test",
                    default_model=model,
                    # No openrouter_api_key_env_var set
                )

    def test_allows_moderate_models_without_byok(self) -> None:
        """Should allow moderate-cost models without BYOK."""
        moderate_models = [
            "anthropic/claude-sonnet-4.5",
            "anthropic/claude-haiku-4.5",
            "openai/gpt-4",
            "google/gemini-pro",
        ]
        for model in moderate_models:
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=model,
                # No BYOK - should still work for moderate models
            )
            assert config.default_model == model

    def test_allows_no_model_without_byok(self) -> None:
        """Should allow no model specified without BYOK."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            # No default_model, no BYOK
        )
        assert config.default_model is None

    def test_allows_cheaper_variants_with_expensive_prefix(self) -> None:
        """Should allow cheaper model variants that share prefix with expensive models."""
        cheaper_variants = [
            "openai/o1-mini",  # Cheaper than o1, but starts with "openai/o1"
            "openai/o1-mini-2024-09-12",  # Dated version of o1-mini
        ]
        for model in cheaper_variants:
            # Should NOT require BYOK for cheaper variants
            config = CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                default_model=model,
                # No BYOK - should work for cheaper models
            )
            assert config.default_model == model


class TestFAQAgentRoleWarning:
    """The two-agent split only saves money if scoring runs on the cheap model.

    With two offered Claude models, the wasteful shape is specifically
    "evaluate everything with the expensive one". The old check warned whenever
    both agents named the same model, which now fires on the recommended
    setup: claude-haiku-4-5 for both is the cheapest valid configuration.
    """

    @staticmethod
    def _faq_config(evaluation_model: str, summary_model: str) -> dict:
        """Two agents, with no temperature set.

        Temperature is left out because it carries its own warning on models
        that ignore it (see TestFAQTemperatureWarning); including it here would
        make the cost-split tests below pass or fail for the wrong reason.
        """
        return {
            "evaluation_agent": {"model": evaluation_model},
            "summary_agent": {"model": summary_model},
        }

    def test_haiku_for_both_agents_is_not_warned_about(self) -> None:
        from src.core.config.community import FAQGenerationConfig

        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            config = FAQGenerationConfig(**self._faq_config("claude-haiku-4-5", "claude-haiku-4-5"))

        assert config.evaluation_agent.model == "claude-haiku-4-5"

    def test_expensive_evaluation_agent_is_warned_about(self) -> None:
        from src.core.config.community import FAQGenerationConfig

        with pytest.warns(UserWarning, match="evaluation_agent uses claude-sonnet-5"):
            FAQGenerationConfig(**self._faq_config("claude-sonnet-5", "claude-sonnet-5"))

    def test_expensive_summary_agent_alone_is_fine(self) -> None:
        """Paying more for the few hundred surviving threads is the intended shape."""
        from src.core.config.community import FAQGenerationConfig

        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            FAQGenerationConfig(**self._faq_config("claude-haiku-4-5", "claude-sonnet-5"))

    def test_provider_field_still_loads_for_backward_compatibility(self) -> None:
        """An existing config.yaml carrying a stale provider hint must not fail
        to load; faq_summarizer logs that it is ignored instead."""
        from src.core.config.community import FAQGenerationConfig

        config = FAQGenerationConfig(
            evaluation_agent={"model": "claude-haiku-4-5", "provider": "DeepInfra/FP8"},
            summary_agent={"model": "claude-haiku-4-5", "provider": "Anthropic"},
        )
        assert config.evaluation_agent.provider == "DeepInfra/FP8"

    def test_legacy_id_for_the_expensive_model_is_still_warned_about(self) -> None:
        """The check is about what gets billed, not about how it is spelled.

        A config that predates the migration and still says
        "anthropic/claude-sonnet-4.5" resolves to claude-sonnet-5 and scores
        every thread at the higher rate, which is exactly the shape this
        warning exists for.
        """
        from src.core.config.community import FAQGenerationConfig

        with pytest.warns(UserWarning, match="evaluation_agent uses") as caught:
            FAQGenerationConfig(
                **self._faq_config("anthropic/claude-sonnet-4.5", "claude-sonnet-5")
            )

        # Both ids, so a maintainer can find the config line and knows what it bills.
        message = str(caught[0].message)
        assert "anthropic/claude-sonnet-4.5" in message
        assert "claude-sonnet-5" in message

    def test_legacy_id_for_the_cheap_model_is_not_warned_about(self) -> None:
        """The mirror case: a legacy Haiku id is the recommended setup."""
        from src.core.config.community import FAQGenerationConfig

        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            FAQGenerationConfig(**self._faq_config("anthropic/claude-haiku-4.5", "claude-sonnet-5"))

    def test_unresolvable_model_is_warned_about(self) -> None:
        """A model the platform will not serve should surface at config load.

        Left as a warning rather than an error on purpose: this schema backs
        the entire community, so raising here would take the community's
        assistant down over a field only FAQ generation reads.
        """
        from src.core.config.community import FAQGenerationConfig

        with pytest.warns(UserWarning, match="evaluation_agent.model is not usable"):
            config = FAQGenerationConfig(
                **self._faq_config("qwen/qwen3-235b-a22b-2507", "claude-haiku-4-5")
            )

        assert config.evaluation_agent.model == "qwen/qwen3-235b-a22b-2507"


class TestFAQTemperatureWarning:
    """A temperature the API never sees should not pass in silence.

    ``claude-sonnet-5`` accepts only its default temperature, so
    ``create_anthropic_llm`` drops the field instead of sending a value that
    would 400. A community that set 0.0 for deterministic scoring is entitled
    to hear that it stopped applying.
    """

    def test_temperature_on_the_expensive_model_is_warned_about(self) -> None:
        from src.core.config.community import FAQGenerationConfig

        with pytest.warns(UserWarning, match="summary_agent.temperature=0.4 is ignored"):
            FAQGenerationConfig(
                evaluation_agent={"model": "claude-haiku-4-5"},
                summary_agent={"model": "claude-sonnet-5", "temperature": 0.4},
            )

    def test_temperature_behind_a_legacy_id_is_warned_about(self) -> None:
        from src.core.config.community import FAQGenerationConfig

        with pytest.warns(UserWarning, match="temperature=0.2 is ignored"):
            FAQGenerationConfig(
                evaluation_agent={"model": "claude-haiku-4-5"},
                summary_agent={"model": "anthropic/claude-sonnet-4.6", "temperature": 0.2},
            )

    def test_temperature_behind_a_legacy_haiku_id_is_silent(self) -> None:
        """The mirror of the case above, and the one an alias table gets wrong.

        "anthropic/claude-haiku-4.5" resolves to a model that does honor a
        temperature, so warning here would be a false alarm on a config that
        works.
        """
        from src.core.config.community import FAQGenerationConfig

        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            FAQGenerationConfig(
                evaluation_agent={"model": "anthropic/claude-haiku-4.5", "temperature": 0.0},
                summary_agent={"model": "claude-haiku-4-5"},
            )

    def test_temperature_on_a_model_that_honors_it_is_silent(self) -> None:
        from src.core.config.community import FAQGenerationConfig

        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            FAQGenerationConfig(
                evaluation_agent={"model": "claude-haiku-4-5", "temperature": 0.0},
                summary_agent={"model": "claude-haiku-4-5", "temperature": 0.1},
            )

    def test_the_fields_own_default_is_not_warned_about(self) -> None:
        """Only a temperature the community actually wrote is worth a warning.

        AgentConfig.temperature defaults to 0.1, which claude-sonnet-5 also
        ignores. Warning about it would fire on every config that names the
        model and sets nothing, which is the recommended summary_agent.
        """
        from src.core.config.community import FAQGenerationConfig

        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            config = FAQGenerationConfig(
                evaluation_agent={"model": "claude-haiku-4-5"},
                summary_agent={"model": "claude-sonnet-5"},
            )

        assert config.summary_agent.temperature == 0.1


class TestClientToolConfig:
    """Tests for ClientToolConfig model."""

    def test_valid_client_tool(self) -> None:
        """Should create a valid client tool entry."""
        tool = ClientToolConfig(
            name="execute_code",
            runtime="python",
            description="Run Python in the browser and return its output.",
        )
        assert tool.name == "execute_code"
        assert tool.runtime == "python"
        assert tool.requires_permission is True

    def test_requires_permission_defaults_true(self) -> None:
        """requires_permission should default to True (safe default)."""
        tool = ClientToolConfig(name="execute_code", runtime="python", description="Run code.")
        assert tool.requires_permission is True

    def test_requires_permission_can_be_disabled(self) -> None:
        """requires_permission should be settable to False."""
        tool = ClientToolConfig(
            name="execute_code",
            runtime="python",
            description="Run code.",
            requires_permission=False,
        )
        assert tool.requires_permission is False

    def test_rejects_unknown_runtime(self) -> None:
        """Should reject a runtime other than the known literal values."""
        with pytest.raises(ValidationError):
            ClientToolConfig(name="execute_code", runtime="javascript", description="Run code.")

    def test_rejects_extra_fields(self) -> None:
        """Should reject unknown fields, matching every sibling model."""
        with pytest.raises(ValidationError):
            ClientToolConfig(
                name="execute_code",
                runtime="python",
                description="Run code.",
                unexpected="nope",
            )

    def test_requires_name_runtime_and_description(self) -> None:
        """name, runtime, and description should all be required."""
        with pytest.raises(ValidationError):
            ClientToolConfig(runtime="python", description="Run code.")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            ClientToolConfig(name="execute_code", description="Run code.")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            ClientToolConfig(name="execute_code", runtime="python")  # type: ignore[call-arg]


class TestRuntimeLimits:
    """Tests for RuntimeLimits model."""

    def test_defaults(self) -> None:
        """Should default to the documented resource caps."""
        limits = RuntimeLimits()
        assert limits.memory_mb == 1536
        assert limits.stdout_chars == 16384
        assert limits.stderr_chars == 8192
        assert limits.images == 3
        assert limits.image_px == 1024
        assert limits.exec_seconds == 120

    def test_accepts_overrides(self) -> None:
        """Should accept explicit values for every field, within the server's caps."""
        limits = RuntimeLimits(
            memory_mb=2048,
            stdout_chars=8192,
            stderr_chars=4096,
            images=2,
            image_px=2048,
            exec_seconds=60,
        )
        assert limits.memory_mb == 2048
        assert limits.images == 2

    @pytest.mark.parametrize(
        ("field", "over_cap"),
        [
            ("stdout_chars", MAX_STDOUT_CHARS + 1),
            ("stderr_chars", MAX_STDERR_CHARS + 1),
            ("images", MAX_IMAGES + 1),
            ("image_px", MAX_IMAGE_EDGE_PX + 1),
        ],
    )
    def test_a_limit_the_server_would_reject_is_refused_at_config_load(
        self, field: str, over_cap: int
    ) -> None:
        """A community must not be able to promise the browser more than the server
        accepts.

        Otherwise the browser honors its own config, sends a result the server rejects
        whole with a 422, and the failure looks like the browser misbehaving when it is
        the config lying. Bounding the config against the same constants makes that
        state unrepresentable rather than merely unlikely.
        """
        with pytest.raises(ValidationError):
            RuntimeLimits(**{field: over_cap})

    def test_the_defaults_are_the_servers_caps(self) -> None:
        """So the common case needs no thought and cannot drift."""
        limits = RuntimeLimits()

        assert limits.stdout_chars == MAX_STDOUT_CHARS
        assert limits.stderr_chars == MAX_STDERR_CHARS
        assert limits.images == MAX_IMAGES

    def test_memory_and_time_are_deliberately_unbounded_here(self) -> None:
        """Neither is a server-side cap: the server never sees memory usage, and
        `exec_seconds` is the browser's own clock. Bounding them against a server
        constant would invent a limit that nothing enforces."""
        limits = RuntimeLimits(memory_mb=99_999, exec_seconds=99_999)

        assert limits.memory_mb == 99_999
        assert limits.exec_seconds == 99_999

    def test_images_can_be_zero(self) -> None:
        """images=0 (no images allowed) should be a valid, explicit choice."""
        limits = RuntimeLimits(images=0)
        assert limits.images == 0

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("memory_mb", 0),
            ("stdout_chars", 0),
            ("stderr_chars", 0),
            ("images", -1),
            ("image_px", 0),
            ("exec_seconds", 0),
        ],
    )
    def test_rejects_below_lower_bound(self, field: str, bad_value: int) -> None:
        """Every field should reject a value below its documented lower bound."""
        with pytest.raises(ValidationError):
            RuntimeLimits(**{field: bad_value})

    def test_rejects_extra_fields(self) -> None:
        """Should reject unknown fields, matching every sibling model."""
        with pytest.raises(ValidationError):
            RuntimeLimits(unexpected="nope")


class TestPythonRuntimeConfig:
    """Tests for PythonRuntimeConfig model."""

    def test_valid_config_with_explicit_limits(self) -> None:
        """An explicit limits section should be honored."""
        config = PythonRuntimeConfig(
            pyodide_version="0.29.5",
            lockfile="pyodide-lock-2026-01.json",
            limits=RuntimeLimits(),
        )
        assert config.pyodide_version == "0.29.5"
        assert config.limits.memory_mb == 1536

    def test_omitted_limits_defaults_to_runtime_limits_defaults(self) -> None:
        """Omitting limits entirely should validate and take every RuntimeLimits default.

        Every field inside RuntimeLimits already has a default, so a
        community with no reason to deviate from them should not have to
        spell out an empty `limits: {}`.
        """
        config = PythonRuntimeConfig(
            pyodide_version="0.29.5",
            lockfile="pyodide-lock-2026-01.json",
        )
        assert config.limits == RuntimeLimits()
        assert config.limits.memory_mb == 1536
        assert config.limits.exec_seconds == 120

    def test_optional_fields_default_empty(self) -> None:
        """preload/allow_install/fetch_allow/index_urls should default to empty lists."""
        config = PythonRuntimeConfig(
            pyodide_version="0.29.5",
            lockfile="pyodide-lock-2026-01.json",
            limits=RuntimeLimits(),
        )
        assert config.preload == []
        assert config.allow_install == []
        assert config.fetch_allow == []
        assert config.index_urls == []
        assert config.preload_on == "first_run"

    def test_preload_on_accepts_widget_open(self) -> None:
        """preload_on should accept 'widget_open' as well as the default."""
        config = PythonRuntimeConfig(
            pyodide_version="0.29.5",
            lockfile="pyodide-lock-2026-01.json",
            preload_on="widget_open",
            limits=RuntimeLimits(),
        )
        assert config.preload_on == "widget_open"

    def test_preload_on_accepts_first_message(self) -> None:
        """preload_on should accept 'first_message': boot as soon as the reader
        sends their first message, overlapping the download with the model's turn."""
        config = PythonRuntimeConfig(
            pyodide_version="0.29.5",
            lockfile="pyodide-lock-2026-01.json",
            preload_on="first_message",
            limits=RuntimeLimits(),
        )
        assert config.preload_on == "first_message"

    def test_rejects_unknown_preload_on(self) -> None:
        """Should reject a preload_on value outside the known literal set."""
        with pytest.raises(ValidationError):
            PythonRuntimeConfig(
                pyodide_version="0.29.5",
                lockfile="pyodide-lock-2026-01.json",
                preload_on="on_click",
                limits=RuntimeLimits(),
            )

    def test_rejects_extra_fields(self) -> None:
        """Should reject unknown fields, matching every sibling model."""
        with pytest.raises(ValidationError):
            PythonRuntimeConfig(
                pyodide_version="0.29.5",
                lockfile="pyodide-lock-2026-01.json",
                limits=RuntimeLimits(),
                unexpected="nope",
            )

    def test_lockfile_and_prelude_are_optional(self) -> None:
        """A runtime the Pyodide distribution alone satisfies needs neither."""
        config = PythonRuntimeConfig(pyodide_version="0.29.5")
        assert config.lockfile is None
        assert config.prelude is None

    def test_lockfile_and_prelude_may_be_given_as_null(self) -> None:
        """A YAML key left empty arrives as None, which means the same as omitting it."""
        config = PythonRuntimeConfig(pyodide_version="0.29.5", lockfile=None, prelude=None)
        assert config.lockfile is None
        assert config.prelude is None

    @pytest.mark.parametrize(
        "lockfile",
        [
            "/etc/lock.json",
            "../other/lock.json",
            "runtime/../../lock.json",
            "./lock.json",
            "runtime\\lock.json",
            "runtime/lock.yaml",
        ],
    )
    def test_a_lockfile_must_stay_in_the_community_folder(self, lockfile: str) -> None:
        """It is joined onto a folder on the server, so it must not be able to leave it."""
        with pytest.raises(ValidationError, match="lockfile"):
            PythonRuntimeConfig(pyodide_version="0.29.5", lockfile=lockfile)

    def test_a_prelude_that_awaits_at_top_level_is_accepted(self) -> None:
        """It runs where executed code runs, and top-level await works there."""
        prelude = "import osa\nstatus, body = await osa.fetch('https://zarr.nemar.org/x')\n"
        assert PythonRuntimeConfig(pyodide_version="0.29.5", prelude=prelude).prelude == prelude

    def test_a_prelude_that_does_not_compile_is_refused_here(self) -> None:
        """Here, at config load, rather than as a failed boot in every reader's browser."""
        with pytest.raises(ValidationError, match="prelude does not compile"):
            PythonRuntimeConfig(pyodide_version="0.29.5", prelude="import osa\nif True\n")

    def test_a_prelude_is_bounded(self) -> None:
        at_the_cap = "x = 1\n" + "#" * (MAX_PRELUDE_CHARS - 7) + "\n"
        assert len(at_the_cap) == MAX_PRELUDE_CHARS
        assert PythonRuntimeConfig(pyodide_version="0.29.5", prelude=at_the_cap).prelude

        with pytest.raises(ValidationError, match="at most"):
            PythonRuntimeConfig(pyodide_version="0.29.5", prelude=at_the_cap + "\n")


class TestRuntimeConfig:
    """Tests for RuntimeConfig model."""

    def test_python_defaults_to_none(self) -> None:
        """A RuntimeConfig with nothing configured should have python=None."""
        config = RuntimeConfig()
        assert config.python is None

    def test_accepts_python_runtime(self) -> None:
        """Should accept a configured python runtime."""
        config = RuntimeConfig(
            python=PythonRuntimeConfig(
                pyodide_version="0.29.5",
                lockfile="pyodide-lock-2026-01.json",
                limits=RuntimeLimits(),
            )
        )
        assert config.python is not None
        assert config.python.pyodide_version == "0.29.5"

    def test_rejects_extra_fields(self) -> None:
        """Should reject unknown fields, matching every sibling model."""
        with pytest.raises(ValidationError):
            RuntimeConfig(unexpected="nope")


class TestExtensionsConfigClientTools:
    """Tests for ExtensionsConfig.client_tools field."""

    def test_defaults_to_empty(self) -> None:
        """client_tools should default to an empty list."""
        config = ExtensionsConfig()
        assert config.client_tools == []

    def test_accepts_client_tools(self) -> None:
        """Should accept a list of client tool entries."""
        config = ExtensionsConfig(
            client_tools=[
                ClientToolConfig(name="execute_code", runtime="python", description="Run code."),
            ]
        )
        assert len(config.client_tools) == 1
        assert config.client_tools[0].name == "execute_code"

    def test_does_not_enforce_uniqueness_itself(self) -> None:
        """Duplicate names are allowed to construct at the ExtensionsConfig
        level: uniqueness is enforced on CommunityConfig, which can see the
        client_tools list as a whole alongside the runtime sibling field
        (see TestCommunityConfigClientTools)."""
        config = ExtensionsConfig(
            client_tools=[
                ClientToolConfig(name="dup", runtime="python", description="One."),
                ClientToolConfig(name="dup", runtime="python", description="Two."),
            ]
        )
        assert len(config.client_tools) == 2

    def test_refuses_more_tools_than_a_request_can_declare(self) -> None:
        """One tool past the cap is refused at load, where an operator sees it,
        rather than as a 422 on every message the widget then sends."""
        tools = [
            ClientToolConfig(name=f"tool_{i}", runtime="python", description="Run.")
            for i in range(MAX_CONFIGURED_CLIENT_TOOLS + 1)
        ]
        with pytest.raises(ValidationError, match="at most"):
            ExtensionsConfig(client_tools=tools)


def _python_runtime_config() -> RuntimeConfig:
    """A minimal valid RuntimeConfig with a python runtime, for reuse below."""
    return RuntimeConfig(
        python=PythonRuntimeConfig(
            pyodide_version="0.29.5",
            lockfile="pyodide-lock-2026-01.json",
            limits=RuntimeLimits(),
        )
    )


def _notebook_kwargs(**overrides: object) -> dict[str, object]:
    """Minimal valid zarr_base/dataset_page_base, for reuse below: only their
    shape matters to these tests, never a real host."""
    kwargs: dict[str, object] = {
        "zarr_base": {"production": "https://zarr.example.org"},
        "dataset_page_base": {"production": "https://example.org"},
    }
    kwargs.update(overrides)
    return kwargs


class TestCommunityConfigClientTools:
    """Tests for CommunityConfig's client_tools/runtime cross-field validator."""

    def test_no_client_tools_no_runtime_required(self) -> None:
        """A community with no client_tools should not need a runtime section."""
        config = CommunityConfig(id="test", name="Test", description="Test")
        assert config.runtime is None

    def test_runtime_optional_when_unused(self) -> None:
        """A community may configure a runtime with no client_tools using it yet."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            runtime=_python_runtime_config(),
        )
        assert config.runtime is not None
        assert config.extensions is None

    def test_client_tools_without_runtime_rejected(self) -> None:
        """client_tools set but no top-level runtime section should fail."""
        with pytest.raises(ValidationError, match="runtime"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                extensions=ExtensionsConfig(
                    client_tools=[
                        ClientToolConfig(
                            name="execute_code", runtime="python", description="Run code."
                        ),
                    ]
                ),
            )

    def test_client_tools_python_without_runtime_python_rejected(self) -> None:
        """A runtime: python client tool needs runtime.python configured."""
        with pytest.raises(ValidationError, match="runtime.python"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                extensions=ExtensionsConfig(
                    client_tools=[
                        ClientToolConfig(
                            name="execute_code", runtime="python", description="Run code."
                        ),
                    ]
                ),
                runtime=RuntimeConfig(),
            )

    def test_client_tools_with_matching_runtime_accepted(self) -> None:
        """client_tools plus a matching runtime.python should validate cleanly."""
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            extensions=ExtensionsConfig(
                client_tools=[
                    ClientToolConfig(
                        name="execute_code", runtime="python", description="Run code."
                    ),
                ]
            ),
            runtime=_python_runtime_config(),
        )
        assert config.extensions.client_tools[0].name == "execute_code"
        assert config.runtime.python is not None

    def test_rejects_duplicate_client_tool_names(self) -> None:
        """Duplicate client_tools names should be rejected on CommunityConfig."""
        with pytest.raises(ValidationError, match="Duplicate client_tools names"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                extensions=ExtensionsConfig(
                    client_tools=[
                        ClientToolConfig(name="execute_code", runtime="python", description="One."),
                        ClientToolConfig(name="execute_code", runtime="python", description="Two."),
                    ]
                ),
                runtime=_python_runtime_config(),
            )


class TestNotebookConfig:
    """Tests for the notebook.osc.earth starter config block (issue #453)."""

    def test_a_minimal_notebook_config_validates(self) -> None:
        config = NotebookConfig(
            starter="notebook/starter.ipynb", dataset_pattern="^nm[0-9]{6}$", **_notebook_kwargs()
        )
        assert config.dataset_pattern == "^nm[0-9]{6}$"

    def test_an_unanchored_pattern_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="anchored"):
            NotebookConfig(
                starter="notebook/starter.ipynb", dataset_pattern="nm[0-9]{6}", **_notebook_kwargs()
            )

    def test_a_pattern_missing_only_the_trailing_dollar_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="anchored"):
            NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern="^nm[0-9]{6}",
                **_notebook_kwargs(),
            )

    def test_a_pattern_that_does_not_compile_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not compile"):
            NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern="^nm[0-9{6}$",
                **_notebook_kwargs(),
            )

    def test_starter_must_be_a_relative_ipynb_path(self) -> None:
        with pytest.raises(ValidationError):
            NotebookConfig(
                starter="/etc/passwd", dataset_pattern="^nm[0-9]{6}$", **_notebook_kwargs()
            )
        with pytest.raises(ValidationError):
            NotebookConfig(
                starter="notebook/starter.json",
                dataset_pattern="^nm[0-9]{6}$",
                **_notebook_kwargs(),
            )

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern="^nm[0-9]{6}$",
                extra_field="nope",
                **_notebook_kwargs(),
            )

    @pytest.mark.parametrize(
        "hostile_pattern",
        [
            "^.*$",  # matches everything, including every probe below
            r'^[a-z0-9"]{1,20}$',  # a quote reachable directly in the character class
            r"^[a-z0-9\\]{1,20}$",  # a backslash reachable directly
            r"^[\s\S]{1,20}$",  # a newline and a space both reachable
        ],
    )
    def test_a_pattern_that_would_accept_a_hostile_probe_is_rejected(
        self, hostile_pattern: str
    ) -> None:
        """Defense in depth (docs/adr/0011-the-notebook-site.md): a dataset id is
        substituted unescaped into the starter's Python source, so a pattern
        this loose must never ship, independent of notebook/open.js's own
        client-side generic shape guard."""
        with pytest.raises(ValidationError, match="hostile probe"):
            NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern=hostile_pattern,
                **_notebook_kwargs(),
            )

    def test_nemars_own_pattern_accepts_no_hostile_probe(self) -> None:
        """The one pattern actually shipped today; a regression here would be
        NEMAR's own config failing to load, not merely a test fixture."""
        config = NotebookConfig(
            starter="notebook/starter.ipynb",
            dataset_pattern="^(nm|ds|on|xx)[0-9]{6}$",
            **_notebook_kwargs(),
        )
        assert config.dataset_pattern == "^(nm|ds|on|xx)[0-9]{6}$"

    def test_an_unrecognized_environment_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unrecognized environment 'staging'"):
            NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern="^nm[0-9]{6}$",
                zarr_base={"staging": "https://zarr-staging.example.org"},
                dataset_page_base={"production": "https://example.org"},
            )

    @pytest.mark.parametrize(
        "malformed",
        [
            "http://zarr.example.org",  # not https
            "https://zarr.example.org/zarr",  # has a path
            "https://zarr.example.org?x=1",  # has a query
            "not-a-url",
        ],
    )
    def test_a_malformed_zarr_base_is_rejected(self, malformed: str) -> None:
        with pytest.raises(ValidationError, match="zarr_base"):
            NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern="^nm[0-9]{6}$",
                zarr_base={"production": malformed},
                dataset_page_base={"production": "https://example.org"},
            )

    def test_a_malformed_dataset_page_base_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="dataset_page_base"):
            NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern="^nm[0-9]{6}$",
                zarr_base={"production": "https://zarr.example.org"},
                dataset_page_base={"production": "https://example.org/dataset"},
            )


class TestCommunityConfigNotebook:
    """Tests for CommunityConfig's notebook/runtime.python.pyodide_version cross-check."""

    def test_no_notebook_no_pyodide_pin_required(self) -> None:
        config = CommunityConfig(id="test", name="Test", description="Test")
        assert config.notebook is None

    def test_notebook_with_matching_pyodide_pin_accepted(self) -> None:
        config = CommunityConfig(
            id="test",
            name="Test",
            description="Test",
            notebook=NotebookConfig(
                starter="notebook/starter.ipynb",
                dataset_pattern="^nm[0-9]{6}$",
                **_notebook_kwargs(),
            ),
            runtime=_python_runtime_config(),
        )
        assert config.notebook is not None
        assert config.runtime.python.pyodide_version == NOTEBOOK_SITE_PYODIDE_VERSION

    def test_notebook_with_no_runtime_at_all_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="One notebook site loads one Pyodide"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                notebook=NotebookConfig(
                    starter="notebook/starter.ipynb",
                    dataset_pattern="^nm[0-9]{6}$",
                    **_notebook_kwargs(),
                ),
            )

    def test_notebook_with_a_different_pyodide_pin_is_rejected(self) -> None:
        mismatched = RuntimeConfig(
            python=PythonRuntimeConfig(pyodide_version="0.28.3", limits=RuntimeLimits())
        )
        with pytest.raises(ValidationError, match="One notebook site loads one Pyodide"):
            CommunityConfig(
                id="test",
                name="Test",
                description="Test",
                notebook=NotebookConfig(
                    starter="notebook/starter.ipynb",
                    dataset_pattern="^nm[0-9]{6}$",
                    **_notebook_kwargs(),
                ),
                runtime=mismatched,
            )
