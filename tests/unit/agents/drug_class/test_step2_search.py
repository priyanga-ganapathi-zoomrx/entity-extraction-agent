"""Unit tests for drug class step 2: search and caching."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.agents.drug_class.step2_search import (
    _normalize_drug_name,
    _get_firms_key,
    _get_cache_path,
    load_search_cache,
    save_search_cache,
)
from src.agents.drug_class.schemas import DrugSearchCache


@pytest.mark.unit
class TestSearchCaching:
    """Test search caching utilities."""

    def test_normalize_drug_name_basic(self):
        """Test drug name normalization."""
        assert _normalize_drug_name("Pembrolizumab") == "pembrolizumab"
        assert _normalize_drug_name("NIVOLUMAB") == "nivolumab"

    def test_normalize_drug_name_with_spaces(self):
        """Test drug name normalization with spaces."""
        assert _normalize_drug_name("Nivolumab Plus Ipilimumab") == "nivolumab_plus_ipilimumab"
        assert _normalize_drug_name("Drug Name") == "drug_name"

    def test_normalize_drug_name_with_special_chars(self):
        """Test drug name normalization with special characters."""
        assert _normalize_drug_name("Drug-A") == "drug_a"
        assert _normalize_drug_name("Drug/B") == "drug_b"
        assert _normalize_drug_name("Drug-A/B") == "drug_a_b"

    def test_normalize_drug_name_with_leading_trailing_spaces(self):
        """Test drug name normalization with leading/trailing spaces."""
        assert _normalize_drug_name("  Pembrolizumab  ") == "pembrolizumab"
        assert _normalize_drug_name("\tDrug\t") == "drug"

    def test_get_firms_key_empty_list(self):
        """Test firms key generation with empty list."""
        assert _get_firms_key([]) == "[]"

    def test_get_firms_key_single_firm(self):
        """Test firms key generation with single firm."""
        import json
        assert _get_firms_key(["Merck"]) == json.dumps(["merck"])

    def test_get_firms_key_multiple_firms_sorted(self):
        """Test firms key generation with multiple firms (should be sorted)."""
        import json
        # Should be sorted alphabetically
        key = _get_firms_key(["Merck", "BMS", "Roche"])
        expected = json.dumps(["bms", "merck", "roche"])
        assert key == expected

    def test_get_firms_key_normalization(self):
        """Test firms key normalization (lowercase, stripped)."""
        import json
        key = _get_firms_key(["  Merck  ", "BMS"])
        expected = json.dumps(["bms", "merck"])
        assert key == expected

    def test_get_firms_key_empty_strings_filtered(self):
        """Test that empty strings are filtered from firms key."""
        import json
        key = _get_firms_key(["Merck", "", "  ", "BMS"])
        expected = json.dumps(["bms", "merck"])
        assert key == expected

    def test_get_cache_path_basic(self):
        """Test cache path generation."""
        path = _get_cache_path("Pembrolizumab")
        assert path == "pembrolizumab.json"

    def test_get_cache_path_with_spaces(self):
        """Test cache path generation with spaces."""
        path = _get_cache_path("Nivolumab Plus Ipilimumab")
        assert path == "nivolumab_plus_ipilimumab.json"

    def test_load_search_cache_exists(self, mock_storage):
        """Test loading existing cache."""
        cache_data = {
            "drug": "Pembrolizumab",
            "fetched_at": "2025-01-01T00:00:00Z",
            "drug_class_search": {
                "fetched_at": "2025-01-01T00:00:00Z",
                "results": [{"url": "test.com", "content": "test"}]
            },
            "firm_searches": {}
        }

        mock_storage.exists.return_value = True
        mock_storage.download_json.return_value = cache_data

        result = load_search_cache("Pembrolizumab", mock_storage)

        assert result is not None
        assert result.drug == "Pembrolizumab"
        assert result.fetched_at == "2025-01-01T00:00:00Z"
        mock_storage.exists.assert_called_once_with("pembrolizumab.json")
        mock_storage.download_json.assert_called_once_with("pembrolizumab.json")

    def test_load_search_cache_not_exists(self, mock_storage):
        """Test loading cache when it doesn't exist."""
        mock_storage.exists.return_value = False

        result = load_search_cache("Pembrolizumab", mock_storage)

        assert result is None
        mock_storage.exists.assert_called_once_with("pembrolizumab.json")
        mock_storage.download_json.assert_not_called()

    def test_load_search_cache_error_handling(self, mock_storage):
        """Test cache loading with error."""
        mock_storage.exists.return_value = True
        mock_storage.download_json.side_effect = Exception("Storage error")

        result = load_search_cache("Pembrolizumab", mock_storage)

        assert result is None  # Should return None on error

    def test_save_search_cache_success(self, mock_storage):
        """Test saving search cache."""
        cache = DrugSearchCache(
            drug="Pembrolizumab",
            fetched_at="2025-01-01T00:00:00Z",
            drug_class_search={},
            firm_searches={}
        )

        save_search_cache("Pembrolizumab", cache, mock_storage)

        mock_storage.upload_json.assert_called_once()
        call_args = mock_storage.upload_json.call_args
        assert call_args[0][0] == "pembrolizumab.json"
        assert call_args[0][1]["drug"] == "Pembrolizumab"

    def test_save_search_cache_with_data(self, mock_storage):
        """Test saving cache with actual search data."""
        cache = DrugSearchCache(
            drug="Pembrolizumab",
            fetched_at="2025-01-01T00:00:00Z",
            drug_class_search={
                "fetched_at": "2025-01-01T00:00:00Z",
                "results": [{"url": "test.com", "content": "PD-1 Inhibitor"}]
            },
            firm_searches={
                '["merck"]': {
                    "results": [{"url": "merck.com", "content": "Merck data"}]
                }
            }
        )

        save_search_cache("Pembrolizumab", cache, mock_storage)

        mock_storage.upload_json.assert_called_once()
        call_args = mock_storage.upload_json.call_args
        saved_data = call_args[0][1]
        assert "drug_class_search" in saved_data
        assert "firm_searches" in saved_data
        assert saved_data["drug_class_search"]["results"][0]["url"] == "test.com"

    def test_save_search_cache_error_handling(self, mock_storage):
        """Test cache saving with error (should not raise)."""
        cache = DrugSearchCache(
            drug="Pembrolizumab",
            fetched_at="2025-01-01T00:00:00Z",
        )

        mock_storage.upload_json.side_effect = Exception("Storage error")

        # Should not raise exception
        save_search_cache("Pembrolizumab", cache, mock_storage)

        mock_storage.upload_json.assert_called_once()

    def test_cache_key_consistency(self):
        """Test that same drug produces same cache key."""
        drug1 = "Pembrolizumab"
        drug2 = "PEMBROLIZUMAB"
        drug3 = "  pembrolizumab  "

        path1 = _get_cache_path(drug1)
        path2 = _get_cache_path(drug2)
        path3 = _get_cache_path(drug3)

        assert path1 == path2 == path3 == "pembrolizumab.json"

    def test_firms_key_consistency(self):
        """Test that same firms produce same key regardless of order."""
        import json

        key1 = _get_firms_key(["Merck", "BMS"])
        key2 = _get_firms_key(["BMS", "Merck"])
        key3 = _get_firms_key(["  merck  ", "  bms  "])

        expected = json.dumps(["bms", "merck"])
        assert key1 == key2 == key3 == expected

    def test_load_save_roundtrip(self, mock_storage):
        """Test that saving and loading cache preserves data."""
        original_cache = DrugSearchCache(
            drug="Pembrolizumab",
            fetched_at="2025-01-01T12:00:00Z",
            drug_class_search={
                "fetched_at": "2025-01-01T12:00:00Z",
                "results": [
                    {"url": "example.com", "content": "PD-1 Inhibitor info"}
                ]
            },
            firm_searches={
                '["merck"]': {
                    "results": [{"url": "merck.com", "content": "Merck specific info"}]
                }
            }
        )

        # Simulate save
        save_search_cache("Pembrolizumab", original_cache, mock_storage)
        saved_data = mock_storage.upload_json.call_args[0][1]

        # Simulate load
        mock_storage.exists.return_value = True
        mock_storage.download_json.return_value = saved_data

        loaded_cache = load_search_cache("Pembrolizumab", mock_storage)

        # Verify data is preserved
        assert loaded_cache.drug == original_cache.drug
        assert loaded_cache.fetched_at == original_cache.fetched_at
        assert loaded_cache.drug_class_search == original_cache.drug_class_search
        assert loaded_cache.firm_searches == original_cache.firm_searches
