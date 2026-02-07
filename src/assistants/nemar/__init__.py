"""NEMAR Assistant - NeuroElectroMagnetic Archive.

Self-contained assistant module for discovering and exploring BIDS-formatted
EEG, MEG, and iEEG datasets hosted on NEMAR (nemar.org).

This module provides specialized Python tools for NEMAR that cannot be
auto-generated from YAML:
- search_nemar_datasets: Search and filter datasets by text, modality, task, etc.
- get_nemar_dataset_details: Get full metadata for a specific dataset

All other configuration (system prompt, CORS, budget) is in config.yaml.
"""

from .tools import get_nemar_dataset_details, search_nemar_datasets

__all__ = [
    "search_nemar_datasets",
    "get_nemar_dataset_details",
]
