"""`osa metrics cost-report`, through typer's CliRunner on a database built with real rows."""

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.cli.main import cli
from src.cli.metrics import open_read_only, parse_since
from src.core.services.anthropic_models import HAIKU, SONNET
from src.metrics.cost import estimate_cost
from src.metrics.db import RequestLogEntry, init_metrics_db, log_request

runner = CliRunner()


def _log(db: Path, request_id: str, model: str, tier, conversation_id, age_days: float) -> float:
    cost = estimate_cost(model, 2000, 200)
    log_request(
        RequestLogEntry(
            request_id=request_id,
            timestamp=(datetime.now(UTC) - timedelta(days=age_days)).isoformat(),
            endpoint="/nemar/chat",
            method="POST",
            community_id="nemar",
            model=model,
            input_tokens=2000,
            output_tokens=200,
            total_tokens=2200,
            estimated_cost=cost,
            tier=tier,
            conversation_id=conversation_id,
        ),
        db,
    )
    return cost


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "metrics.db"
    init_metrics_db(path)
    _log(path, "r1", HAIKU, "quick", "11111111-aaaa", 2)
    _log(path, "r2:quick", HAIKU, "quick", "11111111-aaaa", 1)
    _log(path, "r2", SONNET, "analysis", "11111111-aaaa", 1)
    _log(path, "legacy", HAIKU, None, None, 3)
    return path


class TestCostReport:
    def test_prints_the_table_totals_and_the_rows_without_an_id(self, db) -> None:
        result = runner.invoke(
            cli,
            ["metrics", "cost-report", "--community", "nemar", "--since", "7d", "--db", str(db)],
        )

        assert result.exit_code == 0, result.output
        assert "11111111" in result.output
        assert "2/1" in result.output
        assert "Totals by tier" in result.output
        assert "Quick" in result.output and "Analysis" in result.output
        assert "without a conversation id" in result.output
        assert HAIKU in result.output

    def test_json_output_has_the_conversation_and_the_totals(self, db) -> None:
        result = runner.invoke(
            cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(db), "--json"]
        )

        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        (conversation,) = report["conversations"]
        assert conversation["conversation_id"] == "11111111-aaaa"
        assert conversation["quick"]["turns"] == 2
        assert conversation["analysis"]["cost"] == pytest.approx(estimate_cost(SONNET, 2000, 200))
        assert report["totals"]["total_cost"] == pytest.approx(conversation["total_cost"])
        assert report["unassigned"][0]["model"] == HAIKU

    def test_since_narrows_the_window(self, db) -> None:
        result = runner.invoke(
            cli,
            ["metrics", "cost-report", "-c", "nemar", "--since", "12h", "--db", str(db), "--json"],
        )

        assert json.loads(result.output)["conversations"] == []

    def test_never_writes_to_the_database(self, db) -> None:
        before = db.read_bytes()

        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(db)])

        assert result.exit_code == 0, result.output
        assert db.read_bytes() == before

    def test_the_connection_it_reports_with_refuses_writes(self, db) -> None:
        conn = open_read_only(db)
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute("DELETE FROM request_log")
        finally:
            conn.close()

    def test_reads_a_copy_with_no_wal_files_beside_it(self, db, tmp_path) -> None:
        """What `docker cp` of the live database gives: WAL mode in the header, no `-wal`."""
        copy_dir = tmp_path / "copy"
        copy_dir.mkdir()
        copy = copy_dir / "dev-metrics.db"
        shutil.copyfile(db, copy)

        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(copy)])

        assert result.exit_code == 0, result.output
        assert "11111111" in result.output

    def test_a_database_from_before_the_columns_existed_lists_rows_by_model(self, tmp_path) -> None:
        old = tmp_path / "old.db"
        conn = sqlite3.connect(old)
        conn.execute(
            "CREATE TABLE request_log (id INTEGER PRIMARY KEY, request_id TEXT, timestamp TEXT,"
            " community_id TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,"
            " estimated_cost REAL)"
        )
        conn.execute(
            "INSERT INTO request_log (request_id, timestamp, community_id, model, input_tokens,"
            " output_tokens, estimated_cost) VALUES ('o1', ?, 'nemar', ?, 100, 10, 0.5)",
            (datetime.now(UTC).isoformat(), HAIKU),
        )
        conn.commit()
        conn.close()

        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(old)])

        assert result.exit_code == 0, result.output
        assert "without a conversation id" in result.output

    def test_an_empty_window_says_so(self, db) -> None:
        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "hed", "--db", str(db)])

        assert result.exit_code == 0
        assert "No requests for hed" in result.output

    def test_a_missing_database_is_an_error(self, tmp_path) -> None:
        result = runner.invoke(
            cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(tmp_path / "none.db")]
        )

        assert result.exit_code == 1
        assert "No metrics database" in result.output

    def test_a_bad_since_is_an_error(self, db) -> None:
        result = runner.invoke(
            cli, ["metrics", "cost-report", "-c", "nemar", "--since", "lately", "--db", str(db)]
        )

        assert result.exit_code == 2
        assert "Cannot read --since" in result.output

    def test_a_file_that_is_not_a_database_is_an_error(self, tmp_path) -> None:
        junk = tmp_path / "junk.db"
        junk.write_text("this is not sqlite" * 50)

        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(junk)])

        assert result.exit_code == 1
        assert "Cannot read" in result.output


class TestParseSince:
    NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("text", "delta"),
        [("7d", timedelta(days=7)), ("12h", timedelta(hours=12)), ("2w", timedelta(weeks=2))],
    )
    def test_ages(self, text, delta) -> None:
        assert parse_since(text, self.NOW) == self.NOW - delta

    def test_a_date_is_midnight_utc(self) -> None:
        assert parse_since("2026-11-01", self.NOW) == datetime(2026, 11, 1, tzinfo=UTC)

    def test_nonsense_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Cannot read --since"):
            parse_since("soon", self.NOW)
