"""
Polymarket data client - Gamma API (public, no auth).

Gamma is the discovery/metadata API. It returns outcome prices directly
(no need to hit CLOB for a basic scanner), though Gamma prices can lag
the live order book by a few seconds. That's fine for a mispricing
*scanner* - it is not fine if you were building a latency-sensitive bot.

Pagination: Gamma uses cursor-based keyset pagination
(after_cursor / next_cursor), max page size 100.
"""

import json
from config import GAMMA_API_BASE
from http_utils import get_json


def _safe_json_list(raw):
    """Gamma returns outcomes/outcomePrices as JSON-encoded strings."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def fetch_active_events(max_events: int = 500, page_size: int = 100, tag_id: int = None):
    """
    Pull active (non-closed) events with their nested markets.
    Returns a list of normalized event dicts.

    If tag_id is given, restricts to that category (e.g. crypto, geopolitics)
    - without this, a generic pull tends to get dominated by whatever
    category is most active/trending right now.
    """
    events = []
    cursor = None

    while len(events) < max_events:
        params = {
            "closed": "false",
            "active": "true",
            "limit": min(page_size, max_events - len(events)),
        }
        if cursor:
            params["after_cursor"] = cursor
        if tag_id is not None:
            params["tag_id"] = tag_id

        data = get_json(f"{GAMMA_API_BASE}/events", params=params)

        # Gamma's /events can return either a bare list or a paginated
        # envelope depending on version; handle both defensively.
        if isinstance(data, dict):
            page_events = data.get("data", data.get("events", []))
            cursor = data.get("next_cursor")
        else:
            page_events = data
            cursor = None

        if not page_events:
            break

        for ev in page_events:
            events.append(_normalize_event(ev))

        if not cursor:
            break

    return events


def fetch_events_by_categories(category_tag_ids: dict, categories: list, max_per_category: int):
    """
    Pulls events across MULTIPLE categories (crypto, geopolitics, finance,
    etc.), not just whatever's trending. This is what guarantees your scan
    actually covers the full market instead of being crowded out by
    whatever has the most active markets this week (often politics, since
    that's usually where multi-outcome neg-risk groups are biggest).

    Returns a deduplicated list of normalized events, each tagged with
    which category it was pulled under.
    """
    seen_event_ids = set()
    merged = []

    for category in categories:
        tag_id = category_tag_ids.get(category)
        if tag_id is None:
            continue  # unknown category name - skip rather than guess

        events = fetch_active_events(max_events=max_per_category, tag_id=tag_id)
        for ev in events:
            if ev["event_id"] in seen_event_ids:
                continue  # event can appear under multiple related tags
            seen_event_ids.add(ev["event_id"])
            ev["category"] = category
            merged.append(ev)

    return merged


def _normalize_event(ev: dict) -> dict:
    markets = []
    for m in ev.get("markets", []):
        outcomes = _safe_json_list(m.get("outcomes"))
        prices = _safe_json_list(m.get("outcomePrices"))
        clob_token_ids = _safe_json_list(m.get("clobTokenIds"))

        # Build outcome -> price map defensively (lengths should match,
        # but real-world API data is not always perfectly clean)
        outcome_prices = {}
        for i, name in enumerate(outcomes):
            try:
                outcome_prices[name] = float(prices[i])
            except (IndexError, ValueError, TypeError):
                continue

        markets.append({
            "market_id": m.get("id"),
            "condition_id": m.get("conditionId"),
            "question": m.get("question"),
            "group_item_title": m.get("groupItemTitle"),
            "outcomes": outcomes,
            "outcome_prices": outcome_prices,
            "clob_token_ids": clob_token_ids,
            "liquidity": _to_float(m.get("liquidity")),
            "volume_24h": _to_float(m.get("volume24hr") or m.get("volume24hrClob")),
            "neg_risk": bool(m.get("negRisk", False)),
            "closed": bool(m.get("closed", False)),
            "end_date": m.get("endDate"),
        })

    return {
        "event_id": ev.get("id"),
        "title": ev.get("title"),
        "slug": ev.get("slug"),
        "neg_risk": bool(ev.get("negRisk", False)),
        "markets": markets,
    }


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
