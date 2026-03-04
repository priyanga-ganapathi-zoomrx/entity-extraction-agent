"""Tools for indication extraction rules retrieval.

Supports two modes:
  1. Local file path (default, for CLI / dev use) — loaded and cached via lru_cache
  2. Pre-loaded in-memory data (for Temporal activities) — passed directly, no disk I/O
"""

import csv
from functools import lru_cache
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from src.agents.indication.config import config


@lru_cache(maxsize=32)
def _load_rules_from_file(rules_path: str) -> list[dict]:
    """Load and cache rules from a local CSV file.

    Caches up to 32 distinct paths so multiple rules files
    can coexist across concurrent worker activities.
    Uses utf-8-sig encoding to handle BOM automatically.
    """
    path = Path(rules_path)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [{k.strip(): v.strip() for k, v in row.items()} for row in reader]


def _format_rules(rules: list[dict], category: str, subcategories: list[str]) -> str:
    """Filter and format rules for the LLM tool response."""
    if not rules:
        return "Error: No rules loaded"

    matches = [
        r for r in rules
        if r.get("Category", "") == category.strip()
        and any(s.strip() == r.get("Sub Category", "") for s in subcategories)
    ]

    if not matches:
        return f"No rules found for category '{category}' with subcategories {subcategories}"

    lines = [f"Found {len(matches)} rule(s):\n"]
    for i, r in enumerate(matches, 1):
        lines.append(f"Rule {i} (ID: {r.get('ID', 'N/A')}):")
        lines.append(f"  Keyword: {r.get('Keyword', '')}")
        action_key = "Do/Don't"
        lines.append(f"  Action: {r.get(action_key, '')}")
        lines.append(f"  Generated Rule: {r.get('Generated_Rule', '')}")
        lines.append("")

    return "\n".join(lines)


def get_tools(
    rules_path: Optional[str] = None,
    rules_data: Optional[list[dict]] = None,
) -> list:
    """Get indication extraction tools.

    Two modes:
      - rules_data provided: uses the pre-loaded list[dict] directly (no disk I/O)
      - rules_path provided: loads from local CSV via lru_cache
      - neither: falls back to config.RULES_PATH

    The returned tool function closes over the resolved rules list,
    making it safe for concurrent activities with different rules files.
    """
    resolved_path = rules_path or str(config.RULES_PATH)
    resolved_rules = rules_data

    @tool
    def get_indication_rules(category: str, subcategories: list[str]) -> str:
        """Retrieve clinical rules for indication extraction.

        Args:
            category: Main category (e.g., "Common Check points", "Gene type")
            subcategories: List of subcategories to filter by

        Returns:
            Formatted string with matching rules
        """
        rules = resolved_rules if resolved_rules is not None else _load_rules_from_file(resolved_path)
        return _format_rules(rules, category, subcategories)

    return [get_indication_rules]
