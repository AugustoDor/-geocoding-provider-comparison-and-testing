# Geocoding API Comparison

Script that tests and compares three location autocomplete providers by simulating a user typing a query character by character.

**Providers tested:**
- Mapbox Search Box API
- HERE Autosuggest API
- Google Places Autocomplete API

---

## Requirements

- Python 3.8+
- API keys for the providers you want to test (see below)

---

## Setup

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Configure API keys**
```bash
cp .env.example .env
# edit .env and fill in your keys
```

### Getting API keys

| Provider | Where to sign up | Free tier |
|----------|-----------------|-----------|
| Mapbox   | https://account.mapbox.com/ | 100,000 requests/month |
| HERE     | https://platform.here.com/portal/ | 30,000 requests/month |
| Google   | https://console.cloud.google.com/ | $200 credit/month (~11,700 sessions) |

You only need keys for the providers you want to test. The script skips any provider whose key is missing.

---

## Usage

**Test all providers with the default query ("Buenos Aires")**
```bash
python test_geocoding.py
```

**Test a single provider**
```bash
python test_geocoding.py --provider mapbox
python test_geocoding.py --provider here
python test_geocoding.py --provider google
```

**Test a custom query**
```bash
python test_geocoding.py --query "Cafe Tortoni"
python test_geocoding.py --provider google --query "Palermo"
```

**Run all predefined test queries**
```bash
python test_geocoding.py --all-queries
```
Predefined queries: `Buenos Aires`, `Cafe Tortoni`, `Palermo`, `Aeropuerto Ezeiza`

**Save results to JSON**
```bash
python test_geocoding.py --output results.json
python test_geocoding.py --all-queries --output results_all.json
```

---

## How it works

For each query the script:

1. Breaks the text into incremental queries: `"B"`, `"Bu"`, `"Bue"`, ...
2. Calls the autocomplete endpoint for each partial query
3. Displays the top 3 suggestions and response time per request
4. Selects the first suggestion returned during the session
5. Calls the retrieve/details endpoint to get coordinates and full address
6. Prints a cost estimate based on current published pricing

---

## Language flag

The `language` parameter only affects the **response**, not the input.

Both Mapbox and Google use multilingual indexes, so the query can be written in any language and they will still resolve it to the correct place. For example:

| Input | Language | Returns |
|-------|----------|---------|
| `"Buenos Aires"` | `es` | Buenos Aires, Argentina |
| `"ブエノスアイレス"` (Japanese) | `es` | Buenos Aires, Argentina |
| `"Alemania"` | `en` | Germany |

This is useful for apps with international users: regardless of the language the user types in, you always get the canonical place name back in the language you configured. No special handling needed on the input side.

The script uses `language=es` by default for all providers.

---

## Pricing model (verify at each provider's page — may change)

| Provider | Billing model | Approx. cost per search session |
|----------|--------------|--------------------------------|
| Mapbox   | Per session  | $0.003 |
| HERE     | Per request  | ~$0.006 (12 requests × $0.00049) |
| Google   | Per session (with session tokens) | $0.017 |

A "session" = all autocomplete requests for a single user typing + one retrieve/details call.

**Mapbox and Google both use a session model**, meaning all the intermediate keystrokes are free and you pay once per completed search. HERE charges per individual request.

---

## Output example

```
==================================================
  TESTING: Mapbox Search Box API
==================================================

Request #1 — Query: "B"
├─ Suggestions:
│  ├─ 1. Buenos Aires — Buenos Aires, Argentina
│  ├─ 2. Barcelona — Cataluña, España
│  └─ 3. Berlin — Germany
└─ Response time: 118ms

...

SELECTED: Buenos Aires
├─ Coordinates: -34.6037, -58.3816
├─ Full address: Buenos Aires, Ciudad Autónoma de Buenos Aires, Argentina
└─ Response time: 104ms

SUMMARY:
├─ Total requests: 13
├─ Total time:     1450ms
├─ Errors:         0
└─ Estimated cost: $0.00300  (1 session × $0.003)
```
