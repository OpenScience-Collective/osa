"""Generic YAML-based tests that work for ALL communities.

This test module automatically discovers and tests all registered communities
without requiring community-specific test code. Tests validate:
- Configuration structure and metadata
- Documentation URL accessibility
- GitHub repository existence
- DOI validity
- System prompt completeness
- Tool auto-generation

Adding a new community requires NO test code changes - tests run automatically.
"""

import os
import re
from unittest.mock import MagicMock

import pytest
import requests


def discover_all_communities():
    """Discover all registered community IDs for parametrized testing.

    Clears and repopulates the registry to ensure a fresh state for test
    parameterization. This must happen at collection time, not test execution time.

    Returns:
        List of community IDs found in src/assistants/*/config.yaml files
    """
    from src.assistants import discover_assistants, registry

    registry._assistants.clear()
    discover_assistants()
    return list(registry._assistants.keys())


@pytest.fixture(scope="module", autouse=True)
def setup_registry():
    """Ensure registry is populated before any tests run."""
    from src.assistants import discover_assistants, registry

    registry._assistants.clear()
    discover_assistants()


@pytest.mark.parametrize("community_id", discover_all_communities())
class TestCommunityYAMLConfiguration:
    """Generic tests that validate ANY community's YAML configuration."""

    def test_community_registered(self, community_id):
        """Community should be registered in the registry."""
        from src.assistants import registry

        assert community_id in registry, f"{community_id} not found in registry"

    def test_community_metadata_complete(self, community_id):
        """Community should have required metadata fields."""
        from src.assistants import registry

        info = registry.get(community_id)
        assert info is not None, f"{community_id} info is None"
        assert info.name, f"{community_id} missing name"
        assert info.description, f"{community_id} missing description"
        assert info.status in [
            "available",
            "beta",
            "alpha",
        ], f"{community_id} invalid status: {info.status}"

    def test_community_config_accessible(self, community_id):
        """Community configuration should be retrievable."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)
        assert config is not None, f"Failed to get config for {community_id}"
        assert config.id == community_id
        assert config.name
        assert config.description

    def test_documentation_urls_valid_format(self, community_id):
        """All documentation URLs should be properly formatted."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)
        assert config.documentation, f"{community_id} has no documentation"

        for doc in config.documentation:
            url_str = str(doc.url)
            assert url_str.startswith("http"), f"{community_id}/{doc.title}: Invalid URL"
            if doc.source_url:
                source_url_str = str(doc.source_url)
                assert source_url_str.startswith("http"), (
                    f"{community_id}/{doc.title}: Invalid source_url"
                )

    @pytest.mark.slow
    @pytest.mark.skip(reason="Disabled: upstream HED URL broken (404). See #139")
    def test_documentation_urls_accessible(self, community_id):
        """All documentation source URLs should return HTTP 200.

        This test makes real HTTP requests and is marked as 'slow'.
        Slow tests are included by default in test runs.
        To skip slow tests: pytest -m "not slow"
        """
        from src.assistants import registry

        config = registry.get_community_config(community_id)
        failures = []

        for doc in config.documentation:
            if not doc.source_url:
                continue

            try:
                response = requests.head(doc.source_url, timeout=10, allow_redirects=True)
                if response.status_code != 200:
                    failures.append(
                        f"{doc.title}: {doc.source_url} returned {response.status_code}"
                    )
            except requests.exceptions.RequestException as e:
                failures.append(f"{doc.title}: {doc.source_url} - {e!s}")

        assert not failures, f"{community_id} has broken documentation URLs:\n" + "\n".join(
            failures
        )

    def test_github_repos_valid_format(self, community_id):
        """GitHub repos should be in owner/repo format."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)
        if not config.github or not config.github.repos:
            pytest.skip(f"{community_id} has no GitHub repos configured")

        for repo in config.github.repos:
            assert "/" in repo, f"{community_id}: Invalid repo format: {repo}"
            parts = repo.split("/")
            assert len(parts) == 2, f"{community_id}: Repo must be owner/name: {repo}"
            assert parts[0], f"{community_id}: Missing owner in {repo}"
            assert parts[1], f"{community_id}: Missing repo name in {repo}"

    @pytest.mark.slow
    def test_github_repos_exist(self, community_id):
        """All GitHub repos should exist and be accessible.

        This test makes real GitHub API requests and is marked as 'slow'.
        Slow tests are included by default in test runs.
        To skip slow tests: pytest -m "not slow"
        """
        from src.assistants import registry

        config = registry.get_community_config(community_id)
        if not config.github or not config.github.repos:
            pytest.skip(f"{community_id} has no GitHub repos configured")

        headers = {"Accept": "application/vnd.github.v3+json"}
        github_token = os.environ.get("READ_GITHUB_TOKEN")
        if github_token:
            headers["Authorization"] = f"Bearer {github_token}"

        failures = []
        rate_limited = False
        for repo in config.github.repos:
            try:
                response = requests.get(
                    f"https://api.github.com/repos/{repo}",
                    timeout=10,
                    headers=headers,
                )
                if response.status_code == 403:
                    rate_limited = True
                    break
                if response.status_code == 404:
                    failures.append(f"{repo}: returned {response.status_code}")
            except requests.exceptions.RequestException as e:
                failures.append(f"{repo}: {e!s}")

        if rate_limited:
            pytest.skip("GitHub API rate-limited (403), cannot verify repos")

        assert not failures, f"{community_id} has non-existent GitHub repos:\n" + "\n".join(
            failures
        )

    def test_dois_valid_format(self, community_id):
        """All DOIs should match standard DOI format (10.xxxx/...)."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)
        if not config.citations or not config.citations.dois:
            pytest.skip(f"{community_id} has no DOIs configured")

        for doi in config.citations.dois:
            assert re.match(r"10\.\d{4,}/", doi), f"{community_id}: Invalid DOI format: {doi}"

    def test_system_prompt_no_unfilled_placeholders(self, community_id):
        """System prompt should have all template placeholders substituted."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)
        prompt = assistant.get_system_prompt()

        placeholders = [
            "{repo_list}",
            "{paper_dois}",
            "{preloaded_docs_section}",
            "{available_docs_section}",
            "{page_context_section}",
            "{additional_instructions}",
        ]

        unfilled = [p for p in placeholders if p in prompt]
        assert not unfilled, f"{community_id} has unfilled placeholders: {unfilled}"

    def test_knowledge_tools_generated(self, community_id):
        """Knowledge discovery tools should be auto-generated based on community config."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)
        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)

        tool_names = {t.name for t in assistant.tools}

        # retrieve_docs is always generated when documentation exists
        if config.documentation:
            assert f"retrieve_{community_id}_docs" in tool_names, (
                f"{community_id} missing tool: retrieve_{community_id}_docs"
            )

        # GitHub-dependent tools only when github config exists
        has_github = getattr(config, "github", None)
        if has_github:
            for suffix in ["discussions", "recent"]:
                tool_name = (
                    f"search_{community_id}_{suffix}"
                    if suffix == "discussions"
                    else f"list_{community_id}_{suffix}"
                )
                assert tool_name in tool_names, f"{community_id} missing tool: {tool_name}"

        # Paper search only when citations config exists
        has_citations = getattr(config, "citations", None)
        if has_citations:
            assert f"search_{community_id}_papers" in tool_names, (
                f"{community_id} missing tool: search_{community_id}_papers"
            )

    def test_tools_have_descriptions(self, community_id):
        """All auto-generated tools should have descriptions."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)

        for tool in assistant.tools:
            assert tool.description, f"{community_id}/{tool.name} missing description"

    def test_documentation_categories_valid(self, community_id):
        """Documentation should have valid category values."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)

        for doc in config.documentation:
            assert doc.category, f"{community_id}/{doc.title} missing category"
            assert isinstance(doc.category, str), f"{community_id}/{doc.title} category not string"

    def test_preload_flag_consistent(self, community_id):
        """Preload flag should be boolean and consistent."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)

        for doc in config.documentation:
            if doc.preload is not None:
                assert isinstance(doc.preload, bool), (
                    f"{community_id}/{doc.title} preload must be boolean"
                )

    def test_assistant_creation_succeeds(self, community_id):
        """Should be able to create assistant instance without errors."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)

        assert assistant is not None
        assert assistant.config.id == community_id

    def test_system_prompt_not_empty(self, community_id):
        """System prompt should not be empty."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)

        prompt = assistant.get_system_prompt()
        assert prompt, f"{community_id} has empty system prompt"
        assert len(prompt) > 100, f"{community_id} system prompt suspiciously short"

    def test_status_is_valid(self, community_id):
        """Community status should be one of the allowed values."""
        from src.assistants import registry

        info = registry.get(community_id)
        valid_statuses = {"available", "beta", "alpha"}
        assert info.status in valid_statuses, (
            f"{community_id} invalid status: {info.status}. Must be one of {valid_statuses}"
        )


class TestCommunityYAMLSecurity:
    """Security-focused tests that apply to all communities."""

    @pytest.mark.parametrize("community_id", discover_all_communities())
    def test_no_localhost_urls(self, community_id):
        """Documentation should not contain localhost URLs (SSRF protection)."""
        from src.assistants import registry

        config = registry.get_community_config(community_id)

        forbidden_patterns = [
            "localhost",
            "127.0.0.1",
            "169.254.169.254",  # AWS metadata
            "192.168.",  # Private IP
            "10.",  # Private IP
        ]

        violations = []
        for doc in config.documentation:
            if doc.source_url:
                url_lower = doc.source_url.lower()
                for pattern in forbidden_patterns:
                    if pattern in url_lower:
                        violations.append(f"{doc.title}: {doc.source_url}")

        assert not violations, f"{community_id} has forbidden URLs:\n" + "\n".join(violations)


@pytest.mark.parametrize("community_id", discover_all_communities())
class TestCommunityYAMLBehavioral:
    """Behavioral tests for community functionality beyond config structure."""

    def test_retrieve_docs_tool_description_includes_doc_list(self, community_id):
        """Retrieve docs tool should list available documents in description."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)

        retrieve_tool = next(
            (t for t in assistant.tools if t.name == f"retrieve_{community_id}_docs"),
            None,
        )
        assert retrieve_tool is not None, f"retrieve_{community_id}_docs tool not found"

        config = registry.get_community_config(community_id)
        on_demand_docs = [d for d in config.documentation if not d.preload]

        if on_demand_docs:
            # Verify each on-demand doc appears in the description
            for doc in on_demand_docs[:5]:  # Check first 5 to avoid too slow
                assert doc.title in retrieve_tool.description, (
                    f"Doc '{doc.title}' not in tool description"
                )

    def test_system_prompt_contains_actual_github_repos(self, community_id):
        """System prompt should include actual GitHub repo names when configured."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)
        prompt = assistant.get_system_prompt()

        config = registry.get_community_config(community_id)
        if config.github and config.github.repos:
            # Verify at least one repo appears in prompt
            repo_found = any(repo in prompt for repo in config.github.repos)
            assert repo_found, "No GitHub repos from config found in system prompt"

    def test_knowledge_tools_are_callable(self, community_id):
        """Knowledge discovery tools should be callable functions."""
        from src.assistants import registry

        mock_model = MagicMock()
        assistant = registry.create_assistant(community_id, model=mock_model, preload_docs=False)

        knowledge_tool_names = {
            f"search_{community_id}_discussions",
            f"list_{community_id}_recent",
            f"search_{community_id}_papers",
        }

        for tool in assistant.tools:
            if tool.name in knowledge_tool_names:
                # Verify tool has callable function
                assert hasattr(tool, "func") or hasattr(tool, "_run"), (
                    f"{tool.name} is not callable"
                )
