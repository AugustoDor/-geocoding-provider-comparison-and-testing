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

**1. Create and activate a virtual environment**
```bash
python -m venv venv

# macOS / Linux / Git Bash (Windows)
source venv/bin/activate

# PowerShell (Windows)
venv\Scripts\Activate.ps1
# If you get an execution policy error, run first:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Configure API keys**
```bash
cp .env.example .env
# edit .env and fill in your keys
```

> The venv must be active every time you run the script. You'll see `(venv)` at the start of your terminal prompt when it's active. To deactivate: `deactivate`.

### Getting API keys

You only need keys for the providers you want to test. The script skips any provider whose key is missing.

#### Mapbox
Free tier: 100,000 requests/month

1. Sign up at https://account.mapbox.com/
2. Go to **Tokens** in the dashboard
3. Use the default public token, or click **Create a token**
4. Copy the token (starts with `pk.`) into `MAPBOX_ACCESS_TOKEN`

#### HERE
Free tier: 30,000 requests/month

1. Sign up at https://platform.here.com/portal/
2. Create a new project
3. Go to **Access manager → API keys → Generate API key**
4. Copy the key into `HERE_API_KEY`

#### Google Places
Free tier: $200 credit/month (covers ~11,700 sessions)

1. Go to https://console.cloud.google.com/ and create a project
2. Navigate to **APIs & Services → Library**
3. Search for **Places API** and click **Enable**
4. Go to **APIs & Services → Credentials → Create credentials → API key**
5. Copy the key into `GOOGLE_API_KEY`

> Google requires a billing account to use the Places API, but you won't be charged unless you exceed the $200 monthly free credit.

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
Predefined queries: `Buenos Aires`, `Córdoba`, `Mendoza`

**Test fuzzy matching and order tolerance (edge cases)**
```bash
python test_geocoding.py --edge-cases
python test_geocoding.py --provider mapbox --edge-cases
```
Edge cases are sent as a single complete query (not typed incrementally):
- `"Buemos Aires"` — typo, transposed letters
- `"Bunos Aries"` — multiple errors
- `"Argentina Buenos Aires"` — inverted order, country first
- `"Buenos Aires Argentina"` — natural language order

**Print the raw JSON response from the provider**
```bash
python test_geocoding.py --raw
python test_geocoding.py --provider mapbox --query "Mendoza" --raw
```
Useful to inspect the exact fields each provider returns before deciding which ones to use in your app.

**Save results to JSON**
```bash
python test_geocoding.py --output results.json
python test_geocoding.py --edge-cases --output results_edge.json
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

## Result types

By default the script is configured to return **only cities, states/provinces, and countries** — no streets, POIs, or postal codes. This is enforced via a `types` parameter on each provider.

To change what types of places are returned, edit the relevant function in `test_geocoding.py`:

### Mapbox — `_mapbox_suggest()`
```python
"types": "country,region,place",
```
Available values: `country`, `region` (state/province), `place` (city), `locality` (neighborhood), `address`, `poi`

### Google — `_google_autocomplete()`
```python
"types": "(regions)",
```
Available values:
- `(regions)` — countries, states, cities, postal codes
- `(cities)` — cities only
- `address` — street addresses
- `establishment` — POIs (businesses, landmarks)
- You can also pass a single type like `locality` or `country`

### HERE — `_here_autosuggest()`
```python
"resultTypes": "locality,administrativeArea,countryCode",
```
Available values: `locality` (city), `administrativeArea` (state/province), `countryCode` (country), `street`, `houseNumber`, `postalCode`

To search everything (no filter), simply remove the `types` / `resultTypes` parameter from the params dict.

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
