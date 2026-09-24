"""Which deployment a process is, and per-deployment config values (#480)."""

import pytest
from pydantic import ValidationError

from src.core.config.community import McpServer, PythonRuntimeConfig, RuntimeConfig
from src.core.config.deployment import DEPLOYMENTS, current_deployment, for_deployment


@pytest.fixture(autouse=True)
def _no_deployment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OSA_DEPLOYMENT", raising=False)
    monkeypatch.delenv("ROOT_PATH", raising=False)


class TestCurrentDeployment:
    def test_the_deployments_are_the_notebook_builds_names(self) -> None:
        assert DEPLOYMENTS == ("production", "develop")

    def test_a_process_with_neither_setting_is_production(self) -> None:
        """Local runs and tests read production's hosts, as they did before this."""
        assert current_deployment() == "production"

    @pytest.mark.parametrize(
        ("root_path", "expected"),
        [
            ("/osa-dev", "develop"),
            ("/osa-dev/", "develop"),
            (" /osa-dev ", "develop"),
            ("/osa", "production"),
            ("/osa-dev2", "production"),
            ("/osa-dev/x", "production"),
            ("", "production"),
        ],
    )
    def test_without_osa_deployment_the_dev_mount_is_develop(
        self, monkeypatch: pytest.MonkeyPatch, root_path: str, expected: str
    ) -> None:
        """deploy/auto-update-dev.sh mounts the develop container at /osa-dev."""
        monkeypatch.setenv("ROOT_PATH", root_path)
        assert current_deployment() == expected

    def test_osa_deployment_wins_over_the_mount(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROOT_PATH", "/osa-dev")
        monkeypatch.setenv("OSA_DEPLOYMENT", "production")
        assert current_deployment() == "production"
        monkeypatch.setenv("ROOT_PATH", "/osa")
        monkeypatch.setenv("OSA_DEPLOYMENT", " Develop ")
        assert current_deployment() == "develop"

    def test_an_unknown_deployment_is_an_error_not_a_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OSA_DEPLOYMENT", "staging")
        with pytest.raises(ValueError, match="OSA_DEPLOYMENT='staging'"):
            current_deployment()

    def test_for_deployment_reads_a_map_or_passes_one_value_through(self) -> None:
        both = {"production": "p", "develop": "d"}
        assert [for_deployment(both, d) for d in DEPLOYMENTS] == ["p", "d"]
        assert [for_deployment("one", d) for d in DEPLOYMENTS] == ["one", "one"]


class TestMcpServerUrl:
    def test_one_url_serves_every_deployment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = McpServer(name="x", url="https://mcp.example.org/mcp")
        for deployment in DEPLOYMENTS:
            monkeypatch.setenv("OSA_DEPLOYMENT", deployment)
            assert str(server.resolved_url) == "https://mcp.example.org/mcp"

    def test_a_map_resolves_to_the_running_deployment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = McpServer(
            name="x",
            url={
                "production": "https://mcp.example.org/mcp",
                "develop": "https://mcp-test.example.org/mcp",
            },
        )
        assert str(server.resolved_url) == "https://mcp.example.org/mcp"
        monkeypatch.setenv("ROOT_PATH", "/osa-dev")
        assert str(server.resolved_url) == "https://mcp-test.example.org/mcp"

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ({"production": "https://a.example.org/mcp"}, "url has no value for \\['develop'\\]"),
            (
                {
                    "production": "https://a/mcp",
                    "develop": "https://b/mcp",
                    "staging": "https://c/mcp",
                },
                "url names an unknown deployment \\['staging'\\]",
            ),
            ({"production": "https://a/mcp", "develop": "not a url"}, "develop"),
        ],
    )
    def test_a_map_names_both_deployments_with_valid_urls(self, url: object, message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            McpServer(name="x", url=url)

    def test_a_command_server_has_no_url(self) -> None:
        assert McpServer(name="x", command=["some-server"]).resolved_url is None


class TestPythonRuntimePerDeployment:
    def _runtime(self, **fields: object) -> PythonRuntimeConfig:
        return PythonRuntimeConfig(pyodide_version="0.29.5", **fields)

    def test_one_value_is_left_as_it_is(self) -> None:
        runtime = self._runtime(fetch_allow=["https://a.example.org/"], prelude="x = 1")
        for deployment in DEPLOYMENTS:
            resolved = runtime.for_deployment(deployment)
            assert resolved.fetch_allow == ["https://a.example.org/"]
            assert resolved.prelude == "x = 1"

    def test_maps_resolve_to_one_list_and_one_prelude(self) -> None:
        runtime = self._runtime(
            fetch_allow={"production": ["https://a/"], "develop": ["https://a-test/"]},
            prelude={"production": "x = 1", "develop": "x = 2"},
            preload=["numpy"],
        )
        develop = runtime.for_deployment("develop")
        assert develop.fetch_allow == ["https://a-test/"]
        assert develop.prelude == "x = 2"
        assert develop.preload == ["numpy"]
        production = runtime.for_deployment("production")
        assert (production.fetch_allow, production.prelude) == (["https://a/"], "x = 1")
        # The configured object is not changed by resolving it.
        assert isinstance(runtime.fetch_allow, dict)

    def test_no_prelude_stays_none(self) -> None:
        assert self._runtime().for_deployment("develop").prelude is None

    def test_every_deployments_prelude_is_compiled(self) -> None:
        """A syntax error in the develop prelude fails the config check in production too,
        rather than the first staging reader's boot."""
        with pytest.raises(ValidationError, match="prelude for develop does not compile"):
            self._runtime(prelude={"production": "x = 1", "develop": "def ("})

    def test_every_deployments_prelude_is_bounded(self) -> None:
        from src.core.config.community import MAX_PRELUDE_CHARS

        with pytest.raises(ValidationError, match="at most"):
            self._runtime(prelude={"production": "x = 1", "develop": "#" * (MAX_PRELUDE_CHARS + 1)})

    @pytest.mark.parametrize("field", ["fetch_allow", "prelude"])
    def test_a_map_names_both_deployments(self, field: str) -> None:
        value: object = ["https://a/"] if field == "fetch_allow" else "x = 1"
        with pytest.raises(ValidationError, match=f"{field} has no value for \\['develop'\\]"):
            self._runtime(**{field: {"production": value}})
        with pytest.raises(ValidationError, match=f"{field} names an unknown deployment"):
            self._runtime(**{field: {"production": value, "develop": value, "prod": value}})

    def test_a_runtime_without_python_resolves_to_itself(self) -> None:
        empty = RuntimeConfig()
        assert empty.for_deployment("develop") is empty
