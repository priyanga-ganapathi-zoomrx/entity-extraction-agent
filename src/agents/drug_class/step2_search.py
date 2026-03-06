"""Step 2 Search: Tavily search with congress-level caching.

Handles drug class and firm searches via Tavily API.
Results are cached per congress at {drug}.json (relative to storage base_prefix)
to avoid duplicate Tavily calls within the same congress while ensuring freshness
across different congresses.

Cache path (when called from Temporal activity):
    congress/{congress_id}/search_cache/{drug}.json

This module exports:
- fetch_search_results(): Main entry point (cache-aware)
- search_drug_class(): Direct Tavily search for drug class info
- search_drug_firm(): Direct Tavily search for drug + firm info

Includes timeout (120s) and retry (1 retry) handling for Tavily API calls.
"""

import json
from datetime import datetime, timezone

from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_fixed

from src.agents.core import settings
from src.agents.core.ems_logger import get_logger
from src.agents.core.storage import StorageClient
from src.agents.drug_class.config import config
from src.agents.drug_class.schemas import DrugSearchCache

ems_logger = get_logger("drug_class_step2_search")


# =============================================================================
# CACHE HELPERS
# =============================================================================

def _normalize_drug_name(drug: str) -> str:
    """Normalize drug name for cache key.
    
    Converts to lowercase, replaces spaces and slashes with underscores.
    """
    return drug.lower().strip().replace(" ", "_").replace("/", "_").replace("-", "_")


def _get_firms_key(firms: list[str]) -> str:
    """Get consistent key for firms list.
    
    Returns JSON string of sorted, normalized firms for use as cache key.
    """
    normalized = sorted([f.strip().lower() for f in firms if f.strip()])
    return json.dumps(normalized)


def _get_cache_path(drug: str) -> str:
    """Get cache file path for a drug (relative to storage base_prefix)."""
    return f"{_normalize_drug_name(drug)}.json"


def load_search_cache(drug: str, storage: StorageClient) -> DrugSearchCache | None:
    """Load cached search results for a drug.
    
    Args:
        drug: Drug name
        storage: StorageClient instance
        
    Returns:
        DrugSearchCache if found, None otherwise
    """
    cache_path = _get_cache_path(drug)
    try:
        if storage.exists(cache_path):
            data = storage.download_json(cache_path)
            ems_logger.info("cache_loaded", drug=drug, cache_path=cache_path)
            return DrugSearchCache(**data)
    except Exception as e:
        print(f"    ⚠ Could not load cache for {drug}: {e}")
    return None


def save_search_cache(drug: str, cache: DrugSearchCache, storage: StorageClient) -> None:
    """Save search cache for a drug.
    
    Args:
        drug: Drug name
        cache: DrugSearchCache to save
        storage: StorageClient instance
    """
    cache_path = _get_cache_path(drug)
    try:
        storage.upload_json(cache_path, cache.model_dump())
        ems_logger.info("cache_saved", drug=drug, cache_path=cache_path)
    except Exception as e:
        ems_logger.error(
            "cache_save_failed",
            drug=drug,
            cache_path=cache_path,
            error=str(e),
            exc_info=True,
        )


# =============================================================================
# TAVILY SEARCH FUNCTIONS
# =============================================================================

@retry(
    stop=stop_after_attempt(2),  # 1 initial + 1 retry
    wait=wait_fixed(1),  # 1 second between retries
    retry=retry_if_exception_type((TimeoutError, ConnectionError, Exception)),
    reraise=True,
)
def search_drug_class(drug: str) -> list[dict]:
    """Search for drug class information via Tavily.
    
    Query: "{drug}" AND ("Mechanism of Action" OR "Pharmacologic Class" OR ...)
    Searches FDA, NIH, and ClinicalTrials.gov domains.
    
    Args:
        drug: Drug name to search
        
    Returns:
        List of search result dictionaries with title, url, content, raw_content
    """
    try:
        from tavily import TavilyClient
        
        api_key = settings.tavily.TAVILY_API_KEY
        if not api_key:
            ems_logger.error("tavily_api_key_missing", drug=drug)
            return []
        
        client = TavilyClient(api_key=api_key)
        
        query = (
            f'("{drug}" AND '
            f'("Mechanism of Action" OR "12.1 Mechanism of Action" OR "MoA" OR "mode of action" OR '
            f'"Pharmacologic Class" OR "Pharmacological Class" OR "Chemical Class" OR '
            f'"Therapeutic Class" OR "Drug Class"))'
        )
        
        print(f"    🔍 Tavily drug class search: {drug}")
        response = client.search(
            query=query,
            search_depth=config.TAVILY_SEARCH_DEPTH,
            include_domains=["nih.gov", "fda.gov", "clinicaltrials.gov"],
            include_raw_content=True,
            max_results=config.TAVILY_MAX_RESULTS,
        )
        
        results = []
        for result in response.get("results", [])[:config.TAVILY_MAX_RESULTS]:
            results.append({
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "content": result.get("content", ""),
                "raw_content": result.get("raw_content", ""),
            })
        
        print(f"    ✓ Found {len(results)} drug class results")
        return results

    except ImportError:
        ems_logger.error("tavily_not_installed", drug=drug)
        return []
    except Exception as e:
        ems_logger.error(
            "tavily_drug_class_search_failed",
            drug=drug,
            error=str(e),
            exc_info=True,
        )
        return []


@retry(
    stop=stop_after_attempt(2),  # 1 initial + 1 retry
    wait=wait_fixed(1),  # 1 second between retries
    retry=retry_if_exception_type((TimeoutError, ConnectionError, Exception)),
    reraise=True,
)
def search_drug_firm(drug: str, firms: list[str]) -> list[dict]:
    """Search for drug + firm information via Tavily.
    
    Query format:
    - Single firm: ({drug} AND {firm})
    - Multiple firms: ({drug} AND ({firm1} OR {firm2} OR ...))
    
    Args:
        drug: Drug name
        firms: List of firm/company names
        
    Returns:
        List of search result dictionaries
    """
    if not firms:
        return []
    
    try:
        from tavily import TavilyClient
        
        api_key = settings.tavily.TAVILY_API_KEY
        if not api_key:
            return []
        
        client = TavilyClient(api_key=api_key)
        
        # Build query based on number of firms
        if len(firms) == 1:
            query = f'({drug} AND {firms[0]})'
        else:
            firms_or = " OR ".join(firms)
            query = f'({drug} AND ({firms_or}))'
        
        print(f"    🔍 Tavily firm search: {query[:60]}...")
        response = client.search(
            query=query,
            search_depth=config.TAVILY_SEARCH_DEPTH,
            include_raw_content=True,
            max_results=config.TAVILY_MAX_RESULTS,
        )
        
        results = []
        for result in response.get("results", [])[:config.TAVILY_MAX_RESULTS]:
            results.append({
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "content": result.get("content", ""),
                "raw_content": result.get("raw_content", ""),
            })
        
        print(f"    ✓ Found {len(results)} firm search results")
        return results

    except ImportError:
        ems_logger.error("tavily_not_installed", drug=drug)
        return []
    except Exception as e:
        ems_logger.error(
            "tavily_firm_search_failed",
            drug=drug,
            error=str(e),
            exc_info=True,
        )
        return []


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def fetch_search_results(
    drug: str,
    firms: list[str],
    storage: StorageClient,
) -> tuple[list[dict], list[dict]]:
    """Fetch search results for a drug, using cache if available.
    
    This is the main entry point for Step 2 search. It:
    1. Checks the global cache for existing results
    2. Fetches from Tavily if not cached
    3. Saves results to cache for future use
    
    Args:
        drug: Drug name
        firms: List of firm names (can be empty)
        storage: StorageClient for cache access
        
    Returns:
        Tuple of (drug_class_results, firm_search_results)
    """
    firms_key = _get_firms_key(firms)
    now = datetime.now(timezone.utc).isoformat()
    
    # Try to load from cache
    cache = load_search_cache(drug, storage)
    cache_updated = False
    
    if cache is None:
        cache = DrugSearchCache(
            drug=drug,
            fetched_at=now,
        )
    
    # Check if drug class search is cached
    drug_class_results = cache.drug_class_search.get("results", [])
    if not drug_class_results:
        drug_class_results = search_drug_class(drug)
        cache.drug_class_search = {
            "fetched_at": now,
            "results": drug_class_results,
        }
        cache_updated = True
    else:
        ems_logger.info("cache_hit_drug_class", drug=drug)
    
    # Check if firm search is cached (for this specific firms combination)
    firm_search_results = []
    if firms:
        cached_firm = cache.firm_searches.get(firms_key, {})
        firm_search_results = cached_firm.get("results", [])
        
        if not firm_search_results:
            firm_search_results = search_drug_firm(drug, firms)
            cache.firm_searches[firms_key] = {
                "fetched_at": now,
                "firms": firms,
                "results": firm_search_results,
            }
            cache_updated = True
        else:
            ems_logger.info("cache_hit_firm_search", drug=drug)
    
    # Save cache if updated
    if cache_updated:
        save_search_cache(drug, cache, storage)
    
    return drug_class_results, firm_search_results

