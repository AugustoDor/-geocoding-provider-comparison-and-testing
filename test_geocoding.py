"""
Geocoding API Comparison Tool
Tests and compares Mapbox Search Box, HERE Autosuggest, and Google Places Autocomplete APIs.
"""

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import requests
from colorama import Fore, Style, init
from dotenv import load_dotenv

load_dotenv()
init(autoreset=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAPBOX_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN", "")
HERE_API_KEY = os.getenv("HERE_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

REQUEST_DELAY_SECONDS = 0.3  # polite delay between requests

# Set to True via --raw flag to print the raw JSON response from each provider
SHOW_RAW = False

# Pricing reference (verify at each provider's pricing page — may change)
# Mapbox Search Box:  $3.00  per 1,000 sessions  → $0.003 /session
# HERE Geocoding:     $0.49  per 1,000 requests  → $0.00049/request (after free tier)
# Google Places:      $17.00 per 1,000 sessions  → $0.017 /session (session token model)
PRICING = {
    "mapbox": {"per_session": 0.003, "model": "per session"},
    "here":   {"per_request": 0.00049, "model": "per request"},
    "google": {"per_session": 0.017, "model": "per session"},
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Suggestion:
    name: str
    address: str
    place_id: str  # provider-specific ID needed for the retrieve/details call
    country_code: str = ""       # ISO 3166-1 alpha-2 (e.g. "AR")
    country_code_a3: str = ""    # ISO 3166-1 alpha-3 (e.g. "ARG")
    region_code: str = ""        # ISO 3166-2 subdivision part (e.g. "C")
    region_code_full: str = ""   # ISO 3166-2 full (e.g. "AR-C")


@dataclass
class PlaceDetails:
    name: str
    full_address: str
    lat: float
    lng: float
    country_code: str = ""       # ISO 3166-1 alpha-2
    country_code_a3: str = ""    # ISO 3166-1 alpha-3
    region_code: str = ""        # ISO 3166-2 subdivision part
    region_code_full: str = ""   # ISO 3166-2 full


@dataclass
class ProviderStats:
    provider: str
    requests_made: int = 0
    total_time_ms: float = 0.0
    errors: int = 0
    results: list = field(default_factory=list)  # stores raw per-request data for JSON export


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _elapsed_ms(start: float) -> float:
    return round((time.time() - start) * 1000, 1)


def _print_header(title: str) -> None:
    width = 50
    print()
    print(Fore.CYAN + "=" * width)
    print(Fore.CYAN + f"  TESTING: {title}")
    print(Fore.CYAN + "=" * width)


def _print_request(number: int, query: str, suggestions: list[Suggestion], elapsed: float) -> None:
    print(f"\n{Fore.YELLOW}Request #{number}{Style.RESET_ALL} — Query: {Fore.WHITE}\"{query}\"")
    print(Fore.GREEN + "├─ Suggestions:")
    for i, s in enumerate(suggestions[:3], 1):
        connector = "│  └─" if i == len(suggestions[:3]) else "│  ├─"
        iso = ""
        if s.country_code or s.region_code_full or s.region_code:
            codes = [c for c in [s.region_code_full or s.region_code, s.country_code] if c]
            if s.country_code_a3:
                codes.append(s.country_code_a3)
            iso = f"  {Fore.CYAN}[{', '.join(codes)}]"
        print(f"{Fore.GREEN}{connector} {i}. {Style.RESET_ALL}{s.name}{Fore.WHITE} — {s.address}{iso}")
    print(f"{Fore.GREEN}└─ Response time: {Style.RESET_ALL}{elapsed}ms")


def _print_selection(details: PlaceDetails, elapsed: float) -> None:
    print(f"\n{Fore.MAGENTA}SELECTED: {details.name}")
    print(f"{Fore.MAGENTA}├─ Coordinates:  {Style.RESET_ALL}{details.lat}, {details.lng}")
    print(f"{Fore.MAGENTA}├─ Full address: {Style.RESET_ALL}{details.full_address}")
    cc = details.country_code or "—"
    if details.country_code_a3:
        cc += f" / {details.country_code_a3}"
    rc = details.region_code_full or details.region_code or "—"
    print(f"{Fore.MAGENTA}├─ ISO 3166-1:   {Style.RESET_ALL}{cc}")
    print(f"{Fore.MAGENTA}├─ ISO 3166-2:   {Style.RESET_ALL}{rc}")
    print(f"{Fore.MAGENTA}└─ Response time: {Style.RESET_ALL}{elapsed}ms")


def _print_summary(stats: ProviderStats, cost_estimate: float, cost_note: str) -> None:
    print(f"\n{Fore.CYAN}SUMMARY:")
    print(f"{Fore.CYAN}├─ Total requests: {Style.RESET_ALL}{stats.requests_made}")
    print(f"{Fore.CYAN}├─ Total time:     {Style.RESET_ALL}{stats.total_time_ms:.0f}ms")
    print(f"{Fore.CYAN}├─ Errors:         {Style.RESET_ALL}{stats.errors}")
    print(f"{Fore.CYAN}└─ Estimated cost: {Style.RESET_ALL}${cost_estimate:.5f}  ({cost_note})")


def _print_error(message: str) -> None:
    print(Fore.RED + f"  [ERROR] {message}")


def _print_raw(data: dict) -> None:
    print(Fore.WHITE + Style.DIM + "  RAW RESPONSE:")
    print(Fore.WHITE + Style.DIM + json.dumps(data, indent=2, ensure_ascii=False))


def _suggestion_to_dict(s: Suggestion) -> dict:
    d = {"name": s.name, "address": s.address, "country_code": s.country_code, "region_code": s.region_code}
    if s.country_code_a3:
        d["country_code_a3"] = s.country_code_a3
    if s.region_code_full:
        d["region_code_full"] = s.region_code_full
    return d


def _incremental_queries(text: str, min_chars: int = 1) -> list[str]:
    """Returns ['B', 'Bu', 'Bue', ...] for a given text."""
    return [text[:i] for i in range(min_chars, len(text) + 1)]


# ---------------------------------------------------------------------------
# Mapbox Search Box API
# https://docs.mapbox.com/api/search/search-box/
# Flow: suggest (n calls) → retrieve (1 call per selection)
# Billing: one session = all suggest calls sharing a session_token + the retrieve call
# ---------------------------------------------------------------------------

def _mapbox_suggest(query: str, session_token: str) -> tuple[list[Suggestion], float]:
    """Return suggestions for a partial query using the Mapbox Search Box suggest endpoint."""
    url = "https://api.mapbox.com/search/searchbox/v1/suggest"
    params = {
        "q": query,
        "session_token": session_token,
        "access_token": MAPBOX_TOKEN,
        "language": "es",
        "limit": 5,
        # country = país, region = estado/provincia, place = ciudad
        "types": "country,region,place",
    }
    start = time.time()
    response = requests.get(url, params=params, timeout=10)
    elapsed = _elapsed_ms(start)
    response.raise_for_status()

    data = response.json()
    if SHOW_RAW:
        _print_raw(data)

    suggestions = []
    for feature in data.get("suggestions", []):
        name = feature.get("name", "")
        address_parts = [
            feature.get("place_formatted", ""),
            feature.get("full_address", ""),
        ]
        address = next((a for a in address_parts if a), "")
        place_id = feature.get("mapbox_id", "")
        context = feature.get("context", {})
        country_obj = context.get("country", {})
        region_obj = context.get("region", {})
        suggestions.append(Suggestion(
            name=name, address=address, place_id=place_id,
            country_code=country_obj.get("country_code", ""),
            country_code_a3=country_obj.get("country_code_alpha_3", ""),
            region_code=region_obj.get("region_code", ""),
            region_code_full=region_obj.get("region_code_full", ""),
        ))

    return suggestions, elapsed


def _mapbox_retrieve(mapbox_id: str, session_token: str) -> tuple[PlaceDetails, float]:
    """Retrieve full details for a selected Mapbox suggestion."""
    url = f"https://api.mapbox.com/search/searchbox/v1/retrieve/{mapbox_id}"
    params = {
        "session_token": session_token,
        "access_token": MAPBOX_TOKEN,
    }
    start = time.time()
    response = requests.get(url, params=params, timeout=10)
    elapsed = _elapsed_ms(start)
    response.raise_for_status()

    data = response.json()
    feature = data["features"][0]
    props = feature["properties"]
    coords = feature["geometry"]["coordinates"]  # [lng, lat]
    context = props.get("context", {})
    country_obj = context.get("country", {})
    region_obj = context.get("region", {})

    return PlaceDetails(
        name=props.get("name", ""),
        full_address=props.get("full_address", props.get("place_formatted", "")),
        lat=coords[1],
        lng=coords[0],
        country_code=country_obj.get("country_code", ""),
        country_code_a3=country_obj.get("country_code_alpha_3", ""),
        region_code=region_obj.get("region_code", ""),
        region_code_full=region_obj.get("region_code_full", ""),
    ), elapsed


def test_mapbox(queries: list[str]) -> ProviderStats:
    _print_header("Mapbox Search Box API")

    if not MAPBOX_TOKEN:
        _print_error("MAPBOX_ACCESS_TOKEN not set — skipping")
        return ProviderStats(provider="mapbox")

    stats = ProviderStats(provider="mapbox")
    # All suggest calls in one typing session share the same session_token.
    # A new session_token on retrieve closes the billing session.
    session_token = str(uuid.uuid4())
    selected_suggestion: Optional[Suggestion] = None

    for i, query in enumerate(queries, 1):
        try:
            suggestions, elapsed = _mapbox_suggest(query, session_token)
            stats.requests_made += 1
            stats.total_time_ms += elapsed
            _print_request(i, query, suggestions, elapsed)

            if i == 1 and suggestions:
                selected_suggestion = suggestions[0]

            stats.results.append({
                "request": i,
                "query": query,
                "suggestions": [_suggestion_to_dict(s) for s in suggestions[:3]],
                "elapsed_ms": elapsed,
            })
        except requests.RequestException as e:
            stats.errors += 1
            _print_error(str(e))

        time.sleep(REQUEST_DELAY_SECONDS)

    # Retrieve details for the first suggestion found during the session
    if selected_suggestion and selected_suggestion.place_id:
        try:
            details, elapsed = _mapbox_retrieve(selected_suggestion.place_id, session_token)
            stats.requests_made += 1
            stats.total_time_ms += elapsed
            _print_selection(details, elapsed)
            stats.results.append({"retrieve": True, "details": details.__dict__, "elapsed_ms": elapsed})
        except requests.RequestException as e:
            stats.errors += 1
            _print_error(f"Retrieve failed: {e}")

    # Mapbox bills per session, not per request
    cost = PRICING["mapbox"]["per_session"]
    _print_summary(stats, cost, f"1 session × ${cost}")
    return stats


# ---------------------------------------------------------------------------
# HERE Autosuggest API
# https://developer.here.com/documentation/geocoding-search-api/api-reference-swagger.html
# Flow: autosuggest (n calls) → lookup (1 call per selection)
# Billing: per request after free tier
# ---------------------------------------------------------------------------

def _here_autosuggest(query: str) -> tuple[list[Suggestion], float]:
    """Return autocomplete suggestions from HERE Autosuggest."""
    url = "https://autosuggest.search.hereapi.com/v1/autosuggest"
    params = {
        "q": query,
        # 'at' is required; using Buenos Aires area as context hint
        "at": "-34.6037,-58.3816",
        "limit": 5,
        "lang": "es",
        # locality = ciudad, administrativeArea = estado/provincia, countryCode = país
        "resultTypes": "locality,administrativeArea,countryCode",
        "apiKey": HERE_API_KEY,
    }
    start = time.time()
    response = requests.get(url, params=params, timeout=10)
    elapsed = _elapsed_ms(start)
    response.raise_for_status()

    data = response.json()
    if SHOW_RAW:
        _print_raw(data)

    suggestions = []
    for item in data.get("items", []):
        title = item.get("title", "")
        addr = item.get("address", {})
        address = addr.get("label", "")
        place_id = item.get("id", "")
        country_code = addr.get("countryCode", "")
        suggestions.append(Suggestion(
            name=title, address=address, place_id=place_id,
            country_code=country_code,
        ))

    return suggestions, elapsed


def _here_lookup(place_id: str) -> tuple[PlaceDetails, float]:
    """Look up full details for a HERE place ID."""
    url = "https://lookup.search.hereapi.com/v1/lookup"
    params = {
        "id": place_id,
        "lang": "es",
        "apiKey": HERE_API_KEY,
    }
    start = time.time()
    response = requests.get(url, params=params, timeout=10)
    elapsed = _elapsed_ms(start)
    response.raise_for_status()

    data = response.json()
    position = data.get("position", {})
    addr = data.get("address", {})
    address_label = addr.get("label", "")
    title = data.get("title", "")
    country_code = addr.get("countryCode", "")

    return PlaceDetails(
        name=title,
        full_address=address_label,
        lat=position.get("lat", 0.0),
        lng=position.get("lng", 0.0),
        country_code=country_code,
    ), elapsed


def test_here(queries: list[str]) -> ProviderStats:
    _print_header("HERE Autosuggest API")

    if not HERE_API_KEY:
        _print_error("HERE_API_KEY not set — skipping")
        return ProviderStats(provider="here")

    stats = ProviderStats(provider="here")
    selected_suggestion: Optional[Suggestion] = None

    for i, query in enumerate(queries, 1):
        try:
            suggestions, elapsed = _here_autosuggest(query)
            stats.requests_made += 1
            stats.total_time_ms += elapsed
            _print_request(i, query, suggestions, elapsed)

            if i == 1 and suggestions:
                selected_suggestion = suggestions[0]

            stats.results.append({
                "request": i,
                "query": query,
                "suggestions": [_suggestion_to_dict(s) for s in suggestions[:3]],
                "elapsed_ms": elapsed,
            })
        except requests.RequestException as e:
            stats.errors += 1
            _print_error(str(e))

        time.sleep(REQUEST_DELAY_SECONDS)

    if selected_suggestion and selected_suggestion.place_id:
        try:
            details, elapsed = _here_lookup(selected_suggestion.place_id)
            stats.requests_made += 1
            stats.total_time_ms += elapsed
            _print_selection(details, elapsed)
            stats.results.append({"lookup": True, "details": details.__dict__, "elapsed_ms": elapsed})
        except requests.RequestException as e:
            stats.errors += 1
            _print_error(f"Lookup failed: {e}")

    price_per_req = PRICING["here"]["per_request"]
    cost = stats.requests_made * price_per_req
    _print_summary(stats, cost, f"{stats.requests_made} requests × ${price_per_req} (after free tier)")
    return stats


# ---------------------------------------------------------------------------
# Google Places Autocomplete API
# https://developers.google.com/maps/documentation/places/web-service/autocomplete
# Flow: autocomplete (n calls, all share one sessiontoken) → place details (1 call)
# Billing: session token model — autocomplete calls within a session are free;
#          you pay $0.017 when you close the session with a Place Details call.
# ---------------------------------------------------------------------------

def _google_autocomplete(query: str, session_token: str) -> tuple[list[Suggestion], float]:
    """Return autocomplete predictions from Google Places API (legacy endpoint)."""
    url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    params = {
        "input": query,
        "sessiontoken": session_token,
        "language": "es",
        # (regions) incluye: país, estado/provincia, ciudad, código postal
        "types": "(regions)",
        "key": GOOGLE_API_KEY,
    }
    start = time.time()
    response = requests.get(url, params=params, timeout=10)
    elapsed = _elapsed_ms(start)
    response.raise_for_status()

    data = response.json()
    if SHOW_RAW:
        _print_raw(data)

    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        raise requests.RequestException(f"Google API error: {data.get('status')} — {data.get('error_message', '')}")

    suggestions = []
    for pred in data.get("predictions", []):
        main_text = pred.get("structured_formatting", {}).get("main_text", pred.get("description", ""))
        secondary_text = pred.get("structured_formatting", {}).get("secondary_text", "")
        place_id = pred.get("place_id", "")
        suggestions.append(Suggestion(name=main_text, address=secondary_text, place_id=place_id))

    return suggestions, elapsed


def _google_place_details(place_id: str, session_token: str) -> tuple[PlaceDetails, float]:
    """Fetch place details from Google Places API. Passing sessiontoken closes the billing session."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,geometry,formatted_address,address_components",
        "sessiontoken": session_token,
        "language": "es",
        "key": GOOGLE_API_KEY,
    }
    start = time.time()
    response = requests.get(url, params=params, timeout=10)
    elapsed = _elapsed_ms(start)
    response.raise_for_status()

    data = response.json()
    if data.get("status") != "OK":
        raise requests.RequestException(f"Google API error: {data.get('status')} — {data.get('error_message', '')}")

    result = data["result"]
    location = result.get("geometry", {}).get("location", {})

    country_code = ""
    region_code = ""
    for comp in result.get("address_components", []):
        types = comp.get("types", [])
        if "country" in types:
            country_code = comp.get("short_name", "")
        if "administrative_area_level_1" in types:
            region_code = comp.get("short_name", "")

    return PlaceDetails(
        name=result.get("name", ""),
        full_address=result.get("formatted_address", ""),
        lat=location.get("lat", 0.0),
        lng=location.get("lng", 0.0),
        country_code=country_code,
        region_code=region_code,
    ), elapsed


def test_google(queries: list[str]) -> ProviderStats:
    _print_header("Google Places Autocomplete API")

    if not GOOGLE_API_KEY:
        _print_error("GOOGLE_API_KEY not set — skipping")
        return ProviderStats(provider="google")

    stats = ProviderStats(provider="google")
    # All autocomplete calls in one typing session must share the same sessiontoken.
    # Sending the sessiontoken on the Place Details call closes the session and
    # triggers a single $0.017 charge instead of per-request autocomplete charges.
    session_token = str(uuid.uuid4())
    selected_suggestion: Optional[Suggestion] = None

    for i, query in enumerate(queries, 1):
        try:
            suggestions, elapsed = _google_autocomplete(query, session_token)
            stats.requests_made += 1
            stats.total_time_ms += elapsed
            _print_request(i, query, suggestions, elapsed)

            if i == 1 and suggestions:
                selected_suggestion = suggestions[0]

            stats.results.append({
                "request": i,
                "query": query,
                "suggestions": [_suggestion_to_dict(s) for s in suggestions[:3]],
                "elapsed_ms": elapsed,
            })
        except requests.RequestException as e:
            stats.errors += 1
            _print_error(str(e))

        time.sleep(REQUEST_DELAY_SECONDS)

    if selected_suggestion and selected_suggestion.place_id:
        try:
            details, elapsed = _google_place_details(selected_suggestion.place_id, session_token)
            stats.requests_made += 1
            stats.total_time_ms += elapsed
            _print_selection(details, elapsed)
            stats.results.append({"details": True, "place": details.__dict__, "elapsed_ms": elapsed})
        except requests.RequestException as e:
            stats.errors += 1
            _print_error(f"Place details failed: {e}")

    # With session tokens, Google charges per session (not per autocomplete call).
    cost = PRICING["google"]["per_session"]
    _print_summary(stats, cost, f"1 session × ${cost} (session token model)")
    return stats


# ---------------------------------------------------------------------------
# Multi-query runner
# ---------------------------------------------------------------------------

PROVIDER_MAP = {
    "mapbox": test_mapbox,
    "here":   test_here,
    "google": test_google,
}

TEST_QUERIES = {
    "Buenos Aires": "Major city — should appear immediately",
    "Córdoba":      "City with accent — tests unicode handling",
    "Mendoza":      "City that is also a province — tests disambiguation",
}

# Sent as a single complete query (not incrementally) to test fuzzy matching and order tolerance.
EDGE_CASE_QUERIES = {
    "Buemos Aires":       "Typo — transposed letters in city name",
    "Bunos Aries":        "Typo — multiple errors",
    "Argentina Buenos Aires": "Inverted order — country before city",
    "Buenos Aires Argentina": "Natural language order — city then country",
}


def run_provider(provider: str, text: str) -> ProviderStats:
    queries = _incremental_queries(text)
    return PROVIDER_MAP[provider](queries)


def run_all_providers(text: str) -> dict[str, ProviderStats]:
    results = {}
    for provider in PROVIDER_MAP:
        results[provider] = run_provider(provider, text)
    return results


def print_comparison_table(all_stats: dict[str, ProviderStats]) -> None:
    print()
    print(Fore.CYAN + "=" * 60)
    print(Fore.CYAN + "  PROVIDER COMPARISON")
    print(Fore.CYAN + "=" * 60)
    print(f"\n{'Provider':<12} {'Requests':>10} {'Avg latency':>14} {'Errors':>8} {'Est. cost':>12}")
    print("-" * 60)
    for provider, stats in all_stats.items():
        avg = stats.total_time_ms / stats.requests_made if stats.requests_made else 0
        if provider == "mapbox":
            cost = PRICING["mapbox"]["per_session"]
        elif provider == "here":
            cost = stats.requests_made * PRICING["here"]["per_request"]
        else:
            cost = PRICING["google"]["per_session"]
        print(f"{provider:<12} {stats.requests_made:>10} {avg:>12.1f}ms {stats.errors:>8} ${cost:>11.5f}")


def save_results(all_stats: dict[str, ProviderStats], output_path: str) -> None:
    data = {
        provider: {
            "requests_made": s.requests_made,
            "total_time_ms": s.total_time_ms,
            "errors": s.errors,
            "results": s.results,
        }
        for provider, s in all_stats.items()
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n{Fore.GREEN}Results saved to {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare geocoding autocomplete APIs: Mapbox, HERE, and Google Places."
    )
    parser.add_argument(
        "--provider",
        choices=list(PROVIDER_MAP.keys()) + ["all"],
        default="all",
        help="Which provider to test (default: all)",
    )
    parser.add_argument(
        "--query",
        default="Buenos Aires",
        help='Text to type incrementally (default: "Buenos Aires")',
    )
    parser.add_argument(
        "--all-queries",
        action="store_true",
        help="Run all predefined test queries instead of a single one",
    )
    parser.add_argument(
        "--edge-cases",
        action="store_true",
        help="Run edge case queries: typos and inverted city/country order",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the raw JSON response from the provider on each request",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Save results as JSON to this file path",
    )
    return parser


def main() -> None:
    global SHOW_RAW
    args = build_parser().parse_args()
    SHOW_RAW = args.raw

    incremental_queries: dict[str, str]
    single_queries: dict[str, str]

    if args.all_queries:
        incremental_queries = TEST_QUERIES
    else:
        incremental_queries = {args.query: ""}

    single_queries = EDGE_CASE_QUERIES if args.edge_cases else {}

    all_stats: dict[str, ProviderStats] = {}

    for text, description in incremental_queries.items():
        if description:
            print(f"\n{Fore.WHITE + Style.BRIGHT}Query: \"{text}\"  —  {description}")

        if args.provider == "all":
            stats = run_all_providers(text)
        else:
            stats = {args.provider: run_provider(args.provider, text)}

        for provider, s in stats.items():
            if provider not in all_stats:
                all_stats[provider] = s
            else:
                all_stats[provider].requests_made += s.requests_made
                all_stats[provider].total_time_ms += s.total_time_ms
                all_stats[provider].errors += s.errors
                all_stats[provider].results.extend(s.results)

    for text, description in single_queries.items():
        print(f"\n{Fore.WHITE + Style.BRIGHT}Edge case: \"{text}\"  —  {description}")
        providers = list(PROVIDER_MAP.keys()) if args.provider == "all" else [args.provider]
        stats = {}
        for provider in providers:
            # Single full query — not broken into incremental keystrokes
            stats[provider] = PROVIDER_MAP[provider]([text])

        for provider, s in stats.items():
            if provider not in all_stats:
                all_stats[provider] = s
            else:
                all_stats[provider].requests_made += s.requests_made
                all_stats[provider].total_time_ms += s.total_time_ms
                all_stats[provider].errors += s.errors
                all_stats[provider].results.extend(s.results)

    if len(all_stats) > 1:
        print_comparison_table(all_stats)

    if args.output:
        save_results(all_stats, args.output)


if __name__ == "__main__":
    main()
