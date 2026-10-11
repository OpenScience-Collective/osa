"""Tests for the knowledge database module.

These tests use a temporary database to avoid affecting the real knowledge database.
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.db import (
    get_connection,
    get_stats,
    init_db,
    is_db_populated,
    update_sync_metadata,
    upsert_github_item,
    upsert_paper,
)


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "knowledge" / "test.db"

    with patch("src.knowledge.db.get_db_path", return_value=db_path):
        init_db()
        yield db_path


class TestInitDb:
    """Tests for database initialization."""

    def test_init_creates_database_file(self, tmp_path: Path):
        """Test that init_db creates the database file."""
        db_path = tmp_path / "knowledge" / "hed.db"

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()
            assert db_path.exists()

    def test_init_creates_tables(self, tmp_path: Path):
        """Test that init_db creates all required tables."""
        db_path = tmp_path / "knowledge" / "hed.db"

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()

            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            conn.close()

            assert "github_items" in tables
            assert "github_items_fts" in tables
            assert "papers" in tables
            assert "papers_fts" in tables
            assert "sync_metadata" in tables

    def test_init_is_idempotent(self, tmp_path: Path):
        """Test that init_db can be called multiple times safely."""
        db_path = tmp_path / "knowledge" / "hed.db"

        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            init_db()
            init_db()  # Should not raise
            assert db_path.exists()


class TestUpsertGithubItem:
    """Tests for upserting GitHub items."""

    def test_insert_new_issue(self, temp_db: Path):
        """Test inserting a new GitHub issue."""
        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_db),
            get_connection() as conn,
        ):
            upsert_github_item(
                conn,
                repo="hed-standard/hed-specification",
                item_type="issue",
                number=123,
                title="Test issue",
                first_message="This is a test issue body",
                status="open",
                url="https://github.com/hed-standard/hed-specification/issues/123",
                created_at="2024-01-01T00:00:00Z",
            )
            conn.commit()

            row = conn.execute("SELECT * FROM github_items WHERE number = 123").fetchone()

            assert row is not None
            assert row["title"] == "Test issue"
            assert row["status"] == "open"
            assert row["item_type"] == "issue"

    def test_update_existing_issue(self, temp_db: Path):
        """Test updating an existing GitHub issue."""
        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_db),
            get_connection() as conn,
        ):
            # Insert initial
            upsert_github_item(
                conn,
                repo="hed-standard/hed-specification",
                item_type="issue",
                number=123,
                title="Original title",
                first_message="Original body",
                status="open",
                url="https://github.com/hed-standard/hed-specification/issues/123",
                created_at="2024-01-01T00:00:00Z",
            )
            conn.commit()

            # Update
            upsert_github_item(
                conn,
                repo="hed-standard/hed-specification",
                item_type="issue",
                number=123,
                title="Updated title",
                first_message="Updated body",
                status="closed",
                url="https://github.com/hed-standard/hed-specification/issues/123",
                created_at="2024-01-01T00:00:00Z",
            )
            conn.commit()

            row = conn.execute("SELECT * FROM github_items WHERE number = 123").fetchone()

            assert row["title"] == "Updated title"
            assert row["status"] == "closed"

    def test_truncate_long_message(self, temp_db: Path):
        """Test that long messages are truncated."""
        long_message = "x" * 10000

        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_db),
            get_connection() as conn,
        ):
            upsert_github_item(
                conn,
                repo="hed-standard/hed-specification",
                item_type="issue",
                number=456,
                title="Long message issue",
                first_message=long_message,
                status="open",
                url="https://github.com/hed-standard/hed-specification/issues/456",
                created_at="2024-01-01T00:00:00Z",
            )
            conn.commit()

            row = conn.execute(
                "SELECT first_message FROM github_items WHERE number = 456"
            ).fetchone()

            assert len(row["first_message"]) == 5000


class TestUpsertPaper:
    """Tests for upserting papers."""

    def test_insert_new_paper(self, temp_db: Path):
        """Test inserting a new paper."""
        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_db),
            get_connection() as conn,
        ):
            upsert_paper(
                conn,
                source="openalex",
                external_id="W123456",
                title="Test Paper Title",
                first_message="This is the abstract.",
                url="https://doi.org/10.1234/test",
                created_at="2024",
            )
            conn.commit()

            row = conn.execute("SELECT * FROM papers WHERE external_id = 'W123456'").fetchone()

            assert row is not None
            assert row["title"] == "Test Paper Title"
            assert row["source"] == "openalex"

    def test_truncate_long_abstract(self, temp_db: Path):
        """Test that long abstracts are truncated."""
        long_abstract = "x" * 5000

        with (
            patch("src.knowledge.db.get_db_path", return_value=temp_db),
            get_connection() as conn,
        ):
            upsert_paper(
                conn,
                source="openalex",
                external_id="W789",
                title="Long Abstract Paper",
                first_message=long_abstract,
                url="https://doi.org/10.1234/long",
                created_at="2024",
            )
            conn.commit()

            row = conn.execute(
                "SELECT first_message FROM papers WHERE external_id = 'W789'"
            ).fetchone()

            assert len(row["first_message"]) == 2000


class TestGetStats:
    """Tests for database statistics."""

    def test_empty_database_stats(self, temp_db: Path):
        """Test stats for empty database."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            stats = get_stats()

            assert stats["github_total"] == 0
            assert stats["github_issues"] == 0
            assert stats["github_prs"] == 0
            assert stats["papers_total"] == 0

    def test_stats_with_data(self, temp_db: Path):
        """Test stats after adding data."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            with get_connection() as conn:
                # Add some issues
                for i in range(3):
                    upsert_github_item(
                        conn,
                        repo="test/repo",
                        item_type="issue",
                        number=i,
                        title=f"Issue {i}",
                        first_message="Body",
                        status="open" if i < 2 else "closed",
                        url=f"https://github.com/test/repo/issues/{i}",
                        created_at="2024-01-01T00:00:00Z",
                    )

                # Add a PR
                upsert_github_item(
                    conn,
                    repo="test/repo",
                    item_type="pr",
                    number=100,
                    title="Test PR",
                    first_message="Body",
                    status="open",
                    url="https://github.com/test/repo/pull/100",
                    created_at="2024-01-01T00:00:00Z",
                )

                # Add papers
                for i in range(2):
                    upsert_paper(
                        conn,
                        source="openalex",
                        external_id=f"W{i}",
                        title=f"Paper {i}",
                        first_message="Abstract",
                        url=f"https://doi.org/10.1234/{i}",
                        created_at="2024",
                    )

                conn.commit()

            stats = get_stats()

            assert stats["github_total"] == 4
            assert stats["github_issues"] == 3
            assert stats["github_prs"] == 1
            assert stats["github_open"] == 3
            assert stats["papers_total"] == 2
            assert stats["papers_openalex"] == 2


class TestSyncMetadata:
    """Tests for sync metadata tracking."""

    def test_update_sync_metadata(self, temp_db: Path):
        """Test updating sync metadata."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            update_sync_metadata("github", "test/repo", 10)

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM sync_metadata WHERE source_name = 'test/repo'"
                ).fetchone()

                assert row is not None
                assert row["items_synced"] == 10
                assert row["source_type"] == "github"

    def test_update_sync_metadata_records_the_given_time(self, temp_db: Path):
        """A sync that ran long passes its start, which is what the next sync reads."""
        from datetime import UTC, datetime

        from src.knowledge.db import get_last_sync

        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            update_sync_metadata("discourse", "https://f.org", 3, synced_at="2026-01-02T03:04:05")
            assert get_last_sync("discourse", "https://f.org") == "2026-01-02T03:04:05"

            before = datetime.now(UTC).isoformat()
            update_sync_metadata("discourse", "https://f.org", 3)
            assert get_last_sync("discourse", "https://f.org") >= before


class TestProjectNameValidation:
    """Tests for project name validation in get_db_path."""

    def test_valid_project_names(self) -> None:
        """Should accept valid project names."""
        from src.knowledge.db import get_db_path

        # Basic alphanumeric
        assert get_db_path("hed").name == "hed.db"
        assert get_db_path("bids").name == "bids.db"

        # With hyphens
        assert get_db_path("hed-standard").name == "hed-standard.db"

        # With underscores
        assert get_db_path("hed_v2").name == "hed_v2.db"

        # Mixed
        assert get_db_path("hed-test_v2").name == "hed-test_v2.db"

    def test_invalid_empty_project_name(self) -> None:
        """Should reject empty project name."""
        from src.knowledge.db import get_db_path

        with pytest.raises(ValueError, match="Invalid project name"):
            get_db_path("")

    def test_invalid_path_traversal(self) -> None:
        """Should reject project names with path traversal attempts."""
        from src.knowledge.db import get_db_path

        with pytest.raises(ValueError, match="Invalid project name"):
            get_db_path("../../../etc")

    def test_invalid_slashes(self) -> None:
        """Should reject project names containing slashes."""
        from src.knowledge.db import get_db_path

        with pytest.raises(ValueError, match="Invalid project name"):
            get_db_path("hed/bids")

    def test_invalid_special_characters(self) -> None:
        """Should reject project names with special characters."""
        from src.knowledge.db import get_db_path

        with pytest.raises(ValueError, match="Invalid project name"):
            get_db_path("hed@bids")

        with pytest.raises(ValueError, match="Invalid project name"):
            get_db_path("hed.bids")

        with pytest.raises(ValueError, match="Invalid project name"):
            get_db_path("hed bids")


class TestIsDbPopulated:
    """Tests for is_db_populated."""

    def test_nonexistent_db(self, tmp_path: Path):
        """Should return all False when database file does not exist."""
        db_path = tmp_path / "knowledge" / "nonexistent.db"
        with patch("src.knowledge.db.get_db_path", return_value=db_path):
            result = is_db_populated("nonexistent")
        assert all(v is False for v in result.values())
        expected_keys = {"github", "papers", "docstrings", "mailman", "faq", "beps", "discourse"}
        assert set(result.keys()) == expected_keys

    def test_empty_db(self, temp_db: Path):
        """Should return all False for initialized but empty database."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            result = is_db_populated("test")
        # Tables exist but have no rows
        assert all(v is False for v in result.values())

    def test_partially_populated_db(self, temp_db: Path):
        """Should correctly identify which tables have data."""
        with patch("src.knowledge.db.get_db_path", return_value=temp_db):
            # Insert a row directly via SQL
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO github_items (repo, item_type, number, title, url, status, created_at, synced_at) "
                    "VALUES ('test/test', 'issue', 1, 'Test', 'https://example.com', 'open', '2025-01-01', '2025-01-01')"
                )
                conn.commit()

            result = is_db_populated("test")
            assert result["github"] is True
            assert result["papers"] is False
            assert result["docstrings"] is False


class TestDocstringPruning:
    """Deleting docstrings that are gone from the source, and only those."""

    @staticmethod
    def _insert(conn, file_path: str, symbol: str, repo="org/repo", language="matlab") -> None:
        from src.knowledge.db import upsert_docstring

        upsert_docstring(
            conn,
            repo=repo,
            file_path=file_path,
            language=language,
            symbol_name=symbol,
            symbol_type="function",
            docstring=f"{symbol} zebradoc",
        )

    @staticmethod
    def _indexed(conn) -> set[tuple[str, str]]:
        """(repo, symbol) pairs the full-text index finds: a deleted row must leave it too."""
        rows = conn.execute(
            "SELECT d.repo, d.symbol_name FROM docstrings_fts f "
            "JOIN docstrings d ON d.id = f.rowid WHERE docstrings_fts MATCH 'zebradoc'"
        ).fetchall()
        return {(r["repo"], r["symbol_name"]) for r in rows}

    def test_stale_symbols_of_a_parsed_file_are_deleted(self, temp_db: Path):
        """Only the parsed file of this repo and language loses what its parse lacks."""
        from src.knowledge.db import delete_stale_docstrings

        with patch("src.knowledge.db.get_db_path", return_value=temp_db), get_connection() as conn:
            self._insert(conn, "a.m", "kept")
            self._insert(conn, "a.m", "license_only")
            self._insert(conn, "b.m", "other_file")
            self._insert(conn, "a.m", "license_only", repo="other/repo")
            conn.commit()

            deleted = delete_stale_docstrings(
                conn, repo="org/repo", language="matlab", file_path="a.m", keep_symbols={"kept"}
            )
            conn.commit()

            assert deleted == 1
            assert self._indexed(conn) == {
                ("org/repo", "kept"),
                ("org/repo", "other_file"),
                ("other/repo", "license_only"),
            }

    def test_file_that_parses_to_nothing_loses_all_its_docstrings(self, temp_db: Path):
        """A file whose only "help" was a license notice keeps nothing."""
        from src.knowledge.db import delete_stale_docstrings

        with patch("src.knowledge.db.get_db_path", return_value=temp_db), get_connection() as conn:
            self._insert(conn, "lic.m", "lic")
            self._insert(conn, "lic.m", "helper")
            conn.commit()

            deleted = delete_stale_docstrings(
                conn, repo="org/repo", language="matlab", file_path="lic.m", keep_symbols=set()
            )
            conn.commit()

            assert deleted == 2
            assert self._indexed(conn) == set()

    def test_docstrings_of_removed_files_are_deleted(self, temp_db: Path):
        """A file gone from the repo loses its docstrings; other languages and repos keep theirs."""
        from src.knowledge.db import delete_docstrings_of_missing_files

        with patch("src.knowledge.db.get_db_path", return_value=temp_db), get_connection() as conn:
            self._insert(conn, "still_here.m", "present")
            self._insert(conn, "removed.m", "gone")
            self._insert(conn, "module.py", "python_symbol", language="python")
            self._insert(conn, "removed.m", "gone", repo="other/repo")
            conn.commit()

            deleted = delete_docstrings_of_missing_files(
                conn, repo="org/repo", language="matlab", present_files={"still_here.m"}
            )
            conn.commit()

            assert deleted == 1
            assert self._indexed(conn) == {
                ("org/repo", "present"),
                ("org/repo", "python_symbol"),
                ("other/repo", "gone"),
            }
