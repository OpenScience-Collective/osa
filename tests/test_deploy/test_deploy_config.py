"""Tests for deployment configuration files (Phase 3, issue #363).

These parse the files directly rather than eyeballing them, since nobody
runs deploy/docker-compose.yml or .env.example until a deploy breaks.
"""

from pathlib import Path

import yaml

from src.api.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestDockerComposeModelConfig:
    """deploy/docker-compose.yml must not shadow DEFAULT_MODEL from ../.env.

    The environment: block previously hard-coded DEFAULT_MODEL and
    DEFAULT_MODEL_PROVIDER with stale OpenRouter defaults. Because compose
    interpolates ${DEFAULT_MODEL} from deploy/.env (which does not exist),
    that stale default always won over whatever the real .env said.
    Letting env_file supply them instead is both correct and less to keep
    in sync.
    """

    def test_environment_block_sets_no_default_model(self) -> None:
        compose_path = REPO_ROOT / "deploy" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())

        environment = compose["services"]["osa"]["environment"]
        env_keys = {entry.split("=", 1)[0] for entry in environment}

        assert "DEFAULT_MODEL" not in env_keys
        assert "DEFAULT_MODEL_PROVIDER" not in env_keys

    def test_uses_env_file_for_the_real_env(self) -> None:
        compose_path = REPO_ROOT / "deploy" / "docker-compose.yml"
        compose = yaml.safe_load(compose_path.read_text())

        assert compose["services"]["osa"]["env_file"] == ["../.env"]


class TestEnvExampleCoversAnthropicSettings:
    """Every ANTHROPIC_* field on Settings must appear in .env.example.

    A drift test: if a new anthropic_* field is added to Settings without
    documenting it, the first deploy after that change is missed silently.
    Iterates the real Settings model fields rather than hardcoding the
    variable names, per .rules/testing_guidelines.md.
    """

    def test_every_anthropic_settings_field_is_documented(self) -> None:
        env_example_path = REPO_ROOT / ".env.example"
        env_example = env_example_path.read_text()

        anthropic_fields = [name for name in Settings.model_fields if name.startswith("anthropic_")]
        assert anthropic_fields, "Expected at least one anthropic_* Settings field"

        documented_vars = {
            line.split("=", 1)[0].strip()
            for line in env_example.splitlines()
            if "=" in line and not line.strip().startswith("#")
        }

        missing = [name.upper() for name in anthropic_fields if name.upper() not in documented_vars]
        assert missing == [], f"Undocumented ANTHROPIC_* env vars in .env.example: {missing}"

    def test_default_model_is_an_offered_claude_model(self) -> None:
        """DEFAULT_MODEL and TEST_MODEL in .env.example must be real offered ids."""
        from src.core.services.anthropic_llm import OFFERED_MODELS

        env_example_path = REPO_ROOT / ".env.example"
        values = {}
        for line in env_example_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()

        assert values.get("DEFAULT_MODEL") in OFFERED_MODELS
        assert values.get("TEST_MODEL") in OFFERED_MODELS
