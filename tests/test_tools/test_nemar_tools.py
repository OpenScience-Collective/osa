"""Tests for NEMAR dataset discovery tools.

These tests call the real NEMAR API to ensure tools work correctly.
NO MOCKS - we test against the actual service.
"""

import pytest

from src.assistants.nemar.tools import (
    _fetch_all_datasets,
    _matches,
    _parse_sep_field,
    get_nemar_dataset_details,
    search_nemar_datasets,
)


class TestParseHelpers:
    """Tests for internal helper functions."""

    def test_parse_sep_field_with_separator(self):
        """Test splitting multi-value fields with ===NEMAR-SEP=== delimiter."""
        value = "NIH R01===NEMAR-SEP===NSF BCS-123===NEMAR-SEP===ONR N00014"
        result = _parse_sep_field(value)
        assert result == ["NIH R01", "NSF BCS-123", "ONR N00014"]

    def test_parse_sep_field_single_value(self):
        """Test that single values without separator return as-is."""
        result = _parse_sep_field("Single funding source")
        assert result == ["Single funding source"]

    def test_parse_sep_field_empty(self):
        """Test that empty string returns empty list."""
        assert _parse_sep_field("") == []

    def test_parse_sep_field_strips_whitespace(self):
        """Test that whitespace around values is stripped."""
        value = "  A  ===NEMAR-SEP===  B  ===NEMAR-SEP===  C  "
        result = _parse_sep_field(value)
        assert result == ["A", "B", "C"]

    def test_parse_sep_field_skips_empty_parts(self):
        """Test that empty parts between separators are skipped."""
        value = "A===NEMAR-SEP======NEMAR-SEP===B"
        result = _parse_sep_field(value)
        assert result == ["A", "B"]


class TestMatches:
    """Tests for the dataset filter matching logic."""

    @pytest.fixture()
    def sample_dataset(self):
        return {
            "id": "ds001234",
            "name": "Visual attention EEG study",
            "tasks": "attention, rest",
            "modalities": "EEG",
            "readme": "A study of visual attention in healthy adults.",
            "Authors": "Jane Doe, John Smith",
            "hedAnnotation": 0,
            "participants": 30,
        }

    def test_no_filters_matches_all(self, sample_dataset):
        assert _matches(sample_dataset, None, None, None, None, None) is True

    def test_query_matches_name(self, sample_dataset):
        assert _matches(sample_dataset, "visual", None, None, None, None) is True

    def test_query_matches_tasks(self, sample_dataset):
        assert _matches(sample_dataset, "attention", None, None, None, None) is True

    def test_query_matches_readme(self, sample_dataset):
        assert _matches(sample_dataset, "healthy adults", None, None, None, None) is True

    def test_query_matches_authors(self, sample_dataset):
        assert _matches(sample_dataset, "Jane Doe", None, None, None, None) is True

    def test_query_case_insensitive(self, sample_dataset):
        assert _matches(sample_dataset, "VISUAL", None, None, None, None) is True

    def test_query_no_match(self, sample_dataset):
        assert _matches(sample_dataset, "nonexistent_term_xyz", None, None, None, None) is False

    def test_modality_filter_match(self, sample_dataset):
        assert _matches(sample_dataset, None, "EEG", None, None, None) is True

    def test_modality_filter_no_match(self, sample_dataset):
        assert _matches(sample_dataset, None, "MEG", None, None, None) is False

    def test_modality_filter_case_insensitive(self, sample_dataset):
        assert _matches(sample_dataset, None, "eeg", None, None, None) is True

    def test_task_filter_match(self, sample_dataset):
        assert _matches(sample_dataset, None, None, "rest", None, None) is True

    def test_task_filter_no_match(self, sample_dataset):
        assert _matches(sample_dataset, None, None, "gonogo", None, None) is False

    def test_has_hed_true_no_annotation(self, sample_dataset):
        assert _matches(sample_dataset, None, None, None, True, None) is False

    def test_has_hed_true_with_annotation(self, sample_dataset):
        sample_dataset["hedAnnotation"] = 1
        assert _matches(sample_dataset, None, None, None, True, None) is True

    def test_has_hed_none_ignores_filter(self, sample_dataset):
        assert _matches(sample_dataset, None, None, None, None, None) is True

    def test_min_participants_pass(self, sample_dataset):
        assert _matches(sample_dataset, None, None, None, None, 20) is True

    def test_min_participants_fail(self, sample_dataset):
        assert _matches(sample_dataset, None, None, None, None, 50) is False

    def test_combined_filters(self, sample_dataset):
        """Test that multiple filters are ANDed together."""
        assert _matches(sample_dataset, "visual", "EEG", "attention", None, 10) is True
        assert _matches(sample_dataset, "visual", "MEG", "attention", None, 10) is False


class TestFetchAllDatasets:
    """Tests for the NEMAR API fetch function."""

    def test_fetch_returns_list(self):
        """Test that we get a non-empty list of datasets."""
        datasets = _fetch_all_datasets()
        assert isinstance(datasets, list)
        assert len(datasets) > 0

    def test_fetch_dataset_has_required_fields(self):
        """Test that datasets have the expected schema fields."""
        datasets = _fetch_all_datasets()
        ds = datasets[0]

        required_fields = ["id", "name", "modalities", "tasks", "participants"]
        for field in required_fields:
            assert field in ds, f"Missing field: {field}"

    def test_fetch_dataset_count_reasonable(self):
        """Test that dataset count is in a reasonable range."""
        datasets = _fetch_all_datasets()
        # NEMAR has ~485 datasets as of 2025; allow for growth
        assert len(datasets) >= 100
        assert len(datasets) < 5000


class TestSearchNemarDatasets:
    """Tests for the search_nemar_datasets tool against the live API."""

    def test_search_no_filters_returns_results(self):
        """Test that searching without filters returns datasets."""
        result = search_nemar_datasets.invoke({"limit": 5})
        assert "Found **" in result
        assert "ds0" in result  # Dataset IDs start with ds0

    def test_search_by_modality_eeg(self):
        """Test filtering by EEG modality."""
        result = search_nemar_datasets.invoke({"modality_filter": "EEG", "limit": 5})
        assert "Found **" in result
        assert "EEG" in result

    def test_search_by_modality_meg(self):
        """Test filtering by MEG modality."""
        result = search_nemar_datasets.invoke({"modality_filter": "MEG", "limit": 5})
        assert "Found **" in result
        assert "MEG" in result

    def test_search_by_text_query(self):
        """Test text search across dataset fields."""
        result = search_nemar_datasets.invoke({"query": "rest", "limit": 5})
        assert "Found **" in result

    def test_search_has_hed(self):
        """Test filtering for HED-annotated datasets."""
        result = search_nemar_datasets.invoke({"has_hed": True, "limit": 50})
        assert "Found **" in result
        # There are a small number of HED-annotated datasets
        assert "ds0" in result

    def test_search_min_participants(self):
        """Test filtering by minimum participant count."""
        result = search_nemar_datasets.invoke({"min_participants": 100, "limit": 5})
        assert "Found **" in result

    def test_search_no_results(self):
        """Test that a query with no matches returns helpful message."""
        result = search_nemar_datasets.invoke(
            {"query": "zzz_nonexistent_term_that_matches_nothing_xyz"}
        )
        assert "No datasets found" in result
        assert "Total datasets in NEMAR" in result

    def test_search_limit_respected(self):
        """Test that the limit parameter caps results."""
        result = search_nemar_datasets.invoke({"limit": 3})
        assert "(showing 3)" in result

    def test_search_combined_filters(self):
        """Test combining text search with modality filter."""
        result = search_nemar_datasets.invoke(
            {"query": "rest", "modality_filter": "EEG", "limit": 5}
        )
        # Should either find results or report no matches
        assert "Found **" in result or "No datasets found" in result


class TestGetNemarDatasetDetails:
    """Tests for the get_nemar_dataset_details tool against the live API."""

    def test_get_known_dataset(self):
        """Test retrieving a known dataset (ds000248 - MNE sample data)."""
        result = get_nemar_dataset_details.invoke({"dataset_id": "ds000248"})

        assert "ds000248" in result
        assert "openneuro.org/datasets/ds000248" in result
        assert "nemar.org/dataexplorer/detail" in result
        assert "Data Characteristics" in result

    def test_get_dataset_has_metadata(self):
        """Test that retrieved dataset contains expected metadata sections."""
        result = get_nemar_dataset_details.invoke({"dataset_id": "ds000248"})

        assert "Modalities:" in result
        assert "Tasks:" in result
        assert "Participants:" in result
        assert "HED annotations:" in result

    def test_get_dataset_has_links(self):
        """Test that dataset details include OpenNeuro and NEMAR links."""
        result = get_nemar_dataset_details.invoke({"dataset_id": "ds000248"})

        assert "https://openneuro.org/datasets/ds000248" in result
        assert "https://nemar.org/dataexplorer/detail?dataset_id=ds000248" in result

    def test_get_nonexistent_dataset(self):
        """Test that a nonexistent dataset returns a clear message."""
        result = get_nemar_dataset_details.invoke({"dataset_id": "ds999999"})
        assert "not found" in result

    def test_get_dataset_with_hed(self):
        """Test retrieving a dataset known to have HED annotations."""
        # ds002578 has HED annotations
        result = get_nemar_dataset_details.invoke({"dataset_id": "ds002578"})
        assert "HED annotations:" in result
        assert "Yes" in result
