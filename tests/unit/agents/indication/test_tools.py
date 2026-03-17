"""Unit tests for indication extraction tools."""
import pytest
from unittest.mock import patch, MagicMock
from src.agents.indication.tools import (
    _format_rules,
    get_tools,
    _load_rules_from_file,
)


@pytest.fixture
def sample_rules():
    """Sample rules data for testing."""
    from tests.fixtures.indication_responses import SAMPLE_RULES_DATA
    return SAMPLE_RULES_DATA


@pytest.mark.unit
class TestIndicationTools:
    """Test indication extraction tools."""

    def test_format_rules_with_matches(self, sample_rules):
        """Test formatting rules when matches are found."""
        result = _format_rules(sample_rules, "Disease", ["Abbreviations"])

        assert "Found 2 rule(s)" in result
        assert "NSCLC" in result
        assert "RCC" in result
        assert "Non-Small Cell Lung Cancer" in result

    def test_format_rules_single_match(self, sample_rules):
        """Test formatting with single match."""
        result = _format_rules(sample_rules, "Gene type", ["EGFR"])

        assert "Found 1 rule(s)" in result
        assert "EGFR" in result
        assert "mutant" in result

    def test_format_rules_no_matches(self, sample_rules):
        """Test formatting when no rules match."""
        result = _format_rules(sample_rules, "NonExistent", ["Category"])

        assert "No rules found" in result
        assert "NonExistent" in result

    def test_format_rules_empty_rules_list(self):
        """Test formatting with empty rules list."""
        result = _format_rules([], "Disease", ["Abbreviations"])

        assert "Error: No rules loaded" in result

    def test_format_rules_multiple_subcategories(self, sample_rules):
        """Test formatting with multiple subcategories."""
        result = _format_rules(sample_rules, "Gene type", ["EGFR", "BRAF"])

        assert "Found 2 rule(s)" in result
        assert "EGFR" in result
        assert "BRAF" in result

    def test_format_rules_category_case_sensitivity(self, sample_rules):
        """Test that category matching handles whitespace."""
        # Should match even with extra whitespace
        result = _format_rules(sample_rules, "  Disease  ", ["Abbreviations"])

        assert "Found 2 rule(s)" in result

    def test_format_rules_output_structure(self, sample_rules):
        """Test that formatted output includes all required fields."""
        result = _format_rules(sample_rules, "Disease", ["Abbreviations"])

        assert "Rule 1" in result
        assert "ID:" in result
        assert "Keyword:" in result
        assert "Action:" in result
        assert "Generated Rule:" in result

    def test_get_tools_with_in_memory_data(self, sample_rules):
        """Test getting tools with pre-loaded rules data."""
        tools = get_tools(rules_data=sample_rules)

        assert len(tools) == 1
        assert tools[0].name == "get_indication_rules"

        # Test tool invocation
        result = tools[0].invoke({"category": "Disease", "subcategories": ["Abbreviations"]})
        assert "NSCLC" in result

    def test_get_tools_with_file_path(self, tmp_path, sample_rules):
        """Test getting tools with CSV file path."""
        import csv

        # Create temporary CSV file
        csv_file = tmp_path / "test_rules.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            if sample_rules:
                writer = csv.DictWriter(f, fieldnames=sample_rules[0].keys())
                writer.writeheader()
                writer.writerows(sample_rules)

        tools = get_tools(rules_path=str(csv_file))

        assert len(tools) == 1
        result = tools[0].invoke({"category": "Disease", "subcategories": ["Abbreviations"]})
        assert "NSCLC" in result

    def test_get_tools_file_not_found(self, tmp_path):
        """Test getting tools when file doesn't exist."""
        non_existent = tmp_path / "nonexistent.csv"
        tools = get_tools(rules_path=str(non_existent))

        assert len(tools) == 1
        # Should return error message when no rules loaded
        result = tools[0].invoke({"category": "Disease", "subcategories": ["Abbreviations"]})
        assert "Error: No rules loaded" in result

    def test_load_rules_from_file_with_bom(self, tmp_path):
        """Test loading CSV with BOM (utf-8-sig encoding)."""
        import csv

        csv_file = tmp_path / "rules_with_bom.csv"
        # Write with BOM
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Category", "Sub Category", "Keyword", "Do/Don't", "Generated_Rule"])
            writer.writeheader()
            writer.writerow({
                "ID": "1",
                "Category": "Test",
                "Sub Category": "Subtest",
                "Keyword": "test",
                "Do/Don't": "Do",
                "Generated_Rule": "Test rule"
            })

        # Clear cache first
        _load_rules_from_file.cache_clear()

        rules = _load_rules_from_file(str(csv_file))
        assert len(rules) == 1
        assert rules[0]["Category"] == "Test"

    def test_load_rules_from_file_strips_whitespace(self, tmp_path):
        """Test that loaded rules have whitespace stripped."""
        import csv

        csv_file = tmp_path / "rules_whitespace.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["ID", "Category", "Sub Category"])
            writer.writeheader()
            writer.writerow({
                "ID": "  1  ",
                "Category": "  Disease  ",
                "Sub Category": "  Test  "
            })

        # Clear cache
        _load_rules_from_file.cache_clear()

        rules = _load_rules_from_file(str(csv_file))
        assert rules[0]["ID"] == "1"
        assert rules[0]["Category"] == "Disease"
        assert rules[0]["Sub Category"] == "Test"

    def test_load_rules_caching(self, tmp_path, sample_rules):
        """Test that rules are cached with lru_cache."""
        import csv

        csv_file = tmp_path / "cached_rules.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=sample_rules[0].keys())
            writer.writeheader()
            writer.writerows(sample_rules)

        # Clear cache
        _load_rules_from_file.cache_clear()

        # First call - should load from file
        rules1 = _load_rules_from_file(str(csv_file))

        # Second call - should use cache (verify by checking cache_info)
        rules2 = _load_rules_from_file(str(csv_file))

        assert rules1 == rules2
        # Check cache was hit
        cache_info = _load_rules_from_file.cache_info()
        assert cache_info.hits >= 1

    def test_tool_invocation_with_empty_subcategories(self, sample_rules):
        """Test tool invocation with empty subcategories list."""
        tools = get_tools(rules_data=sample_rules)

        result = tools[0].invoke({"category": "Disease", "subcategories": []})
        assert "No rules found" in result

    def test_tool_integration_disease_abbreviations(self, sample_rules):
        """Test complete tool workflow for disease abbreviations."""
        tools = get_tools(rules_data=sample_rules)
        tool = tools[0]

        result = tool.invoke({
            "category": "Disease",
            "subcategories": ["Abbreviations"]
        })

        assert "NSCLC" in result
        assert "Non-Small Cell Lung Cancer" in result
        assert "RCC" in result
        assert "Renal Cell Carcinoma" in result

    def test_tool_integration_gene_types(self, sample_rules):
        """Test complete tool workflow for gene types."""
        tools = get_tools(rules_data=sample_rules)
        tool = tools[0]

        result = tool.invoke({
            "category": "Gene type",
            "subcategories": ["BRAF"]
        })

        assert "BRAF" in result
        assert "V600E" in result
