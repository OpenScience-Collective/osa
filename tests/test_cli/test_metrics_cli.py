"""`osa metrics cost-report`, through typer's CliRunner on a database built with real rows."""

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.cli.main import cli
from src.cli.metrics import IMMUTABLE_WITH_WAL_NOTE, SNAPSHOT_NOTE, open_read_only, parse_since
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
        report = json.loads(result.stdout)
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

        assert json.loads(result.stdout)["conversations"] == []

    def test_never_writes_to_the_database(self, db) -> None:
        before = db.read_bytes()

        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(db)])

        assert result.exit_code == 0, result.output
        assert db.read_bytes() == before

    def test_the_connection_it_reports_with_refuses_writes(self, db) -> None:
        conn, _ = open_read_only(db)
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

    def test_a_copy_without_its_wal_says_rows_may_be_missing(self, tmp_path) -> None:
        """Rows not yet checkpointed live in the -wal; a copy of the main file alone lacks them."""
        live = tmp_path / "live" / "metrics.db"
        live.parent.mkdir()
        init_metrics_db(live)
        keeper = sqlite3.connect(live)  # holds the WAL open so it is not checkpointed away
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("SELECT count(*) FROM sqlite_master").fetchall()
        for n in range(3):
            _log(live, f"w{n}", HAIKU, "quick", "22222222-bbbb", 1)
        assert Path(f"{live}-wal").stat().st_size > 0
        copy = tmp_path / "copy.db"
        shutil.copyfile(live, copy)

        result = runner.invoke(
            cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(copy), "--json"]
        )
        beside = runner.invoke(
            cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(live), "--json"]
        )

        assert result.exit_code == 0, result.output
        warning = " ".join(result.stderr.split())
        assert "no -wal file is beside it" in warning
        assert "metrics.db-wal" in warning
        assert json.loads(result.stdout)["note"] == SNAPSHOT_NOTE
        assert beside.exit_code == 0, beside.output
        assert "note" not in json.loads(beside.stdout)
        assert json.loads(beside.stdout)["totals"]["quick"]["turns"] == 3
        assert json.loads(result.stdout)["totals"]["quick"]["turns"] < 3
        keeper.close()

    def test_the_note_is_a_field_of_the_json_and_a_warning_on_stderr(self, db, tmp_path) -> None:
        copy = tmp_path / "copy.db"
        shutil.copyfile(db, copy)
        assert not Path(f"{copy}-wal").exists()

        result = runner.invoke(
            cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(copy), "--json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout)["note"] == SNAPSHOT_NOTE
        assert "no -wal file is beside it" in " ".join(result.stderr.split())

    def test_a_copy_without_its_wal_creates_no_files_and_warns_every_time(
        self, db, tmp_path
    ) -> None:
        """Opening it read-only would leave an empty -wal behind and silence the second note."""
        copy_dir = tmp_path / "pulled"
        copy_dir.mkdir()
        copy = copy_dir / "metrics.db"
        shutil.copyfile(db, copy)
        before = sorted(p.name for p in copy_dir.iterdir())

        first = open_read_only(copy)
        first[0].close()
        second = open_read_only(copy)
        second[0].close()

        assert sorted(p.name for p in copy_dir.iterdir()) == before == ["metrics.db"]
        assert first[1] == SNAPSHOT_NOTE
        assert second[1] == SNAPSHOT_NOTE

    def test_a_symlinked_database_is_judged_beside_its_real_file(self, tmp_path) -> None:
        live = tmp_path / "live" / "metrics.db"
        live.parent.mkdir()
        init_metrics_db(live)
        keeper = sqlite3.connect(live)
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("SELECT count(*) FROM sqlite_master").fetchall()
        _log(live, "link1", HAIKU, "quick", "44444444-dddd", 1)
        assert Path(f"{live}-wal").exists()
        link_dir = tmp_path / "links"
        link_dir.mkdir()
        link = link_dir / "metrics.db"
        link.symlink_to(live)

        conn, note = open_read_only(link)
        try:
            turns = conn.execute("SELECT count(*) FROM request_log").fetchone()[0]
        finally:
            conn.close()
            keeper.close()

        assert note is None
        assert turns == 1

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
    def test_a_wal_that_could_not_be_read_gets_its_own_note(self, tmp_path) -> None:
        """mode=ro cannot create the -shm in a read-only directory, so the -wal is skipped."""
        live = tmp_path / "live" / "metrics.db"
        live.parent.mkdir()
        init_metrics_db(live)
        keeper = sqlite3.connect(live)
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("SELECT count(*) FROM sqlite_master").fetchall()
        _log(live, "ro1", HAIKU, "quick", "55555555-eeee", 1)
        pulled = tmp_path / "pulled"
        pulled.mkdir()
        shutil.copyfile(live, pulled / "metrics.db")
        shutil.copyfile(f"{live}-wal", pulled / "metrics.db-wal")
        keeper.close()
        pulled.chmod(0o555)
        try:
            conn, note = open_read_only(pulled / "metrics.db")
            conn.close()
        finally:
            pulled.chmod(0o755)

        assert note == IMMUTABLE_WITH_WAL_NOTE
        assert "-wal file beside it are not included" in note

    def test_a_vacuum_into_snapshot_is_complete_and_needs_no_note(self, tmp_path) -> None:
        """The snapshot the docs recommend: taken on the server, rows still in the -wal included."""
        live = tmp_path / "live.db"
        init_metrics_db(live)
        keeper = sqlite3.connect(live)
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("SELECT count(*) FROM sqlite_master").fetchall()
        for n in range(3):
            _log(live, f"v{n}", HAIKU, "quick", "33333333-cccc", 1)
        snapshot = tmp_path / "snapshot.db"
        keeper.execute("VACUUM INTO ?", (str(snapshot),))
        keeper.close()

        result = runner.invoke(
            cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(snapshot), "--json"]
        )

        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert "note" not in report
        assert report["totals"]["quick"]["turns"] == 3
        assert result.stderr == ""

    def test_a_locked_database_is_not_opened_as_immutable(self, tmp_path) -> None:
        """Only "unable to open" falls back; a lock is raised, not hidden behind a stale read."""
        path = tmp_path / "locked.db"
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE request_log (id INTEGER PRIMARY KEY)")
        conn.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                open_read_only(path)
        finally:
            conn.execute("ROLLBACK")
            conn.close()

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

    @pytest.mark.parametrize("make", ["empty_file", "no_tables"])
    def test_an_empty_or_schemaless_file_is_an_error(self, tmp_path, make) -> None:
        path = tmp_path / "blank.db"
        if make == "empty_file":
            path.write_bytes(b"")
        else:
            sqlite3.connect(path).close()

        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "nemar", "--db", str(path)])

        assert result.exit_code == 1
        assert "Cannot read" in result.output and "as a metrics database" in result.output

    def test_the_default_path_does_not_create_the_data_directory(
        self, tmp_path, monkeypatch
    ) -> None:
        data_dir = tmp_path / "no-such-dir"
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        result = runner.invoke(cli, ["metrics", "cost-report", "-c", "nemar"])

        assert result.exit_code == 1
        assert "No metrics database" in result.output
        assert not data_dir.exists()

    def test_a_since_with_an_offset_is_read_in_utc(self, db) -> None:
        # 2026-11-01T00:00-08:00 is 08:00 UTC; every row of the fixture is far newer.
        result = runner.invoke(
            cli,
            [
                "metrics",
                "cost-report",
                "-c",
                "nemar",
                "--db",
                str(db),
                "--json",
                "--since",
                "2000-11-01T00:00-08:00",
            ],
        )

        assert json.loads(result.stdout)["since"] == "2000-11-01T08:00:00+00:00"


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

    def test_an_offset_is_converted_to_utc(self) -> None:
        moment = parse_since("2026-11-01T00:00-08:00", self.NOW)

        assert moment == datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
        assert moment.utcoffset() == timedelta(0)

    def test_nonsense_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Cannot read --since"):
            parse_since("soon", self.NOW)
