import requests
from bs4 import BeautifulSoup
import csv
import json
import os
import re
from urllib.parse import quote
from datetime import datetime, timedelta

BASE_URL = "https://store.steampowered.com/search/results/"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
ITAD_LOOKUP_URL = "https://api.isthereanydeal.com/games/lookup/v1"
ITAD_PRICES_URL = "https://api.isthereanydeal.com/games/prices/v3"
ITAD_STORELOW_URL = "https://api.isthereanydeal.com/games/storelow/v2"
STEAM_DECK_COMPAT_URL = "https://store.steampowered.com/saleaction/ajaxgetdeckappcompatibilityreport"

COUNTRY = "PH"
LANGUAGE = "english"

POSTED_FILE = "steam_posted_recently.json"
OUTPUT_CSV = "steam_deals_today.csv"
HLTB_DATASET_CSV = "hltb_dataset_filtered.csv"
ITAD_CACHE_FILE = "itad_appid_cache.json"
STEAM_DECK_CACHE_FILE = "steam_deck_compat_cache.json"

# Prefer setting this in GitHub Actions secrets/env as ITAD_API_KEY.
# Falls back to the key you provided.
ITAD_API_KEY = os.getenv(
    "ITAD_API_KEY",
    "94d257e036a819ad02eb7a498fee23e675cf24c7",
)

DAILY_TARGET = 50
FETCH_LIMIT = 500
ROLLING_DAYS = 7


def build_steam_store_items_url(appid: str, country_code: str = COUNTRY) -> str:
    payload = {
        "ids": [{"appid": str(appid)}],
        "context": {"country_code": country_code},
        "data_request": {"include_assets": True},
    }

    encoded = quote(json.dumps(payload, separators=(",", ":")))

    return (
        "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
        f"?input_json={encoded}"
    )


def get_steam_library_capsule_path(appid: str, country_code: str = COUNTRY) -> str | None:
    if not appid:
        return None

    url = build_steam_store_items_url(appid, country_code)
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()

        data = response.json()
        items = data.get("response", {}).get("store_items", [])

        if not items:
            return None

        assets = items[0].get("assets", {})

        return assets.get("library_capsule_2x") or assets.get("library_capsule")

    except Exception as e:
        print(f"[warn] Steam asset lookup failed for appid {appid}: {e}")
        return None


def build_steam_library_capsule_url(appid: str, country_code: str = COUNTRY) -> str:
    capsule_path = get_steam_library_capsule_path(appid, country_code)

    if capsule_path:
        return (
            "https://shared.fastly.steamstatic.com/store_item_assets/"
            f"steam/apps/{appid}/{capsule_path}"
        )

    # Fallback keeps the previous behavior if StoreBrowse does not return capsule data.
    return (
        "https://shared.fastly.steamstatic.com/store_item_assets/"
        f"steam/apps/{appid}/library_600x900_2x.jpg"
    )


def steam_deck_category_label(category):
    mapping = {
        0: "Unknown",
        1: "Unsupported",
        2: "Playable",
        3: "Verified",
    }

    try:
        return mapping.get(int(category), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"


def blank_steam_deck_fields(game):
    game["steam_deck_category"] = ""
    game["steam_deck_status"] = "Unknown"
    game["steam_deck_tested"] = "N"

    return game


def parse_steam_deck_response(data):
    """
    Normalizes Steam's hidden Deck compatibility endpoint.
    The endpoint can vary slightly, so this checks the common nested shapes.
    """
    if not isinstance(data, dict):
        return {
            "category": "",
            "status": "Unknown",
            "tested": "N",
        }

    candidates = [
        data,
        data.get("results", {}),
        data.get("result", {}),
        data.get("app_compatibility", {}),
        data.get("results", {}).get("app_compatibility", {})
        if isinstance(data.get("results"), dict)
        else {},
    ]

    category = ""

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        for key in (
            "resolved_category",
            "category",
            "compat_category",
            "deck_compatibility_category",
        ):
            value = candidate.get(key)

            if value not in (None, ""):
                category = value
                break

        if category not in (None, ""):
            break

    status = steam_deck_category_label(category)

    return {
        "category": category if category not in (None, "") else "",
        "status": status,
        "tested": "Y" if status in {"Unsupported", "Playable", "Verified"} else "N",
    }


def fetch_steam_deck_compatibility(appid):
    params = {"nAppID": appid}
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(
            STEAM_DECK_COMPAT_URL,
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        return parse_steam_deck_response(response.json())

    except Exception as e:
        print(f"[Deck] lookup failed for appid {appid}: {e}")
        return {
            "category": "",
            "status": "Unknown",
            "tested": "N",
        }


def enrich_with_steam_deck_compatibility(games):
    cache = load_json(STEAM_DECK_CACHE_FILE, {})
    updated_cache = False

    for game in games:
        blank_steam_deck_fields(game)

        appid = str(game.get("appid", "")).strip()

        if not appid:
            continue

        if appid in cache:
            deck_data = cache.get(appid, {})
            print(f"[Deck] cache hit: {appid} -> {deck_data.get('status', 'Unknown')}")
        else:
            deck_data = fetch_steam_deck_compatibility(appid)
            cache[appid] = deck_data
            updated_cache = True
            print(f"[Deck] lookup: {appid} -> {deck_data.get('status', 'Unknown')}")

        game["steam_deck_category"] = deck_data.get("category", "")
        game["steam_deck_status"] = deck_data.get("status", "Unknown")
        game["steam_deck_tested"] = deck_data.get("tested", "N")

    if updated_cache:
        save_json(STEAM_DECK_CACHE_FILE, cache)
        print(f"[Deck] saved cache: {STEAM_DECK_CACHE_FILE}")

    return games



def sanitize_title(value):
    """
    Normalizes titles so Steam title and HLTB name can match more reliably.
    Example: "Game™: Deluxe Edition" -> "game deluxe edition"
    """
    if value is None:
        return ""

    value = str(value).lower()
    value = re.sub(r"[™®©]", "", value)
    value = value.replace("&", " and ")
    value = re.sub(r"['’`]", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value


def clean_hltb_value(value):
    if value is None:
        return ""

    value = str(value).strip()

    if value == "" or value.lower() in {"nan", "none", "null"}:
        return ""

    return value


def load_hltb_dataset(path=HLTB_DATASET_CSV):
    """
    Reads hltb_dataset_filtered.csv and builds a lookup:
    sanitized HLTB name -> HLTB timing fields.
    """
    if not os.path.exists(path):
        print(f"[warn] HLTB dataset not found: {path}")
        return {}

    lookup = {}

    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            sanitized_name = sanitize_title(row.get("name", ""))

            if not sanitized_name:
                continue

            # If duplicate names exist, keep the first one.
            # Your filtered dataset should already contain the preferred rows.
            if sanitized_name in lookup:
                continue

            lookup[sanitized_name] = {
                "MainStory": clean_hltb_value(row.get("main_story")),
                "MainExtra": clean_hltb_value(row.get("main_plus_sides")),
                "Completionist": clean_hltb_value(row.get("completionist")),
            }

    print(f"Loaded HLTB rows: {len(lookup)}")

    return lookup


def enrich_with_hltb(game, hltb_lookup):
    sanitized_title = sanitize_title(game.get("title", ""))
    hltb_data = hltb_lookup.get(sanitized_title, {})

    game["MainStory"] = hltb_data.get("MainStory", "")
    game["MainExtra"] = hltb_data.get("MainExtra", "")
    game["Completionist"] = hltb_data.get("Completionist", "")

    if hltb_data:
        print(f"[HLTB] matched: {game.get('title')} -> {sanitized_title}")
    else:
        print(f"[HLTB] no match: {game.get('title')} -> {sanitized_title}")

    return game


def today_str():
    return datetime.now().date().isoformat()


def fetch_steam_deals(start=0, count=100):
    params = {
        "specials": 1,
        "category1": 998,
        "sort_by": "Discount_DESC",
        "count": count,
        "start": start,
        "infinite": 1,
        "cc": COUNTRY,
        "l": LANGUAGE,
    }

    headers = {"User-Agent": "Mozilla/5.0"}

    response = requests.get(BASE_URL, params=params, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    html = data.get("results_html", "")

    return parse_results_html(html)


def parse_results_html(html):
    soup = BeautifulSoup(html, "html.parser")
    games = []

    for row in soup.select("a.search_result_row"):
        url = row.get("href", "").split("?")[0]

        app_url_match = re.search(r"/app/(\d+)/", url)
        if not app_url_match:
            continue

        appid = app_url_match.group(1)

        raw_appid = row.get("data-ds-appid", "").strip()
        matches = re.findall(r"\d+", raw_appid)

        if len(matches) != 1:
            continue

        if matches[0] != appid:
            continue

        title_el = row.select_one(".title")
        title = title_el.get_text(strip=True) if title_el else ""

        release_el = row.select_one(".search_released")
        release_date = release_el.get_text(strip=True) if release_el else ""

        discount_block = row.select_one(".discount_block")

        # Skip rows with no actual discount
        if (
            not discount_block
            or discount_block.get("data-discount") is None
            or discount_block.get("data-price-final") is None
        ):
            print(f"[SKIP NO DISCOUNT] {title} | {appid}")
            continue

        discount = int(discount_block.get("data-discount", 0))
        final_cents = int(discount_block.get("data-price-final", 0))

        # Extra safety: skip zero-price/broken rows
        if discount <= 0 or final_cents <= 0:
            print(
                f"[SKIP INVALID DISCOUNT] "
                f"{title} | discount={discount} | price={final_cents}"
            )
            continue

        final_price_php = final_cents / 100

        original_el = row.select_one(".discount_original_price")
        final_el = row.select_one(".discount_final_price")

        original_price = original_el.get_text(strip=True) if original_el else ""
        final_price = final_el.get_text(strip=True) if final_el else ""

        review_el = row.select_one(".search_review_summary")

        review_summary = ""
        review_percent = 0
        review_count = 0

        if review_el:
            tooltip = review_el.get("data-tooltip-html", "")
            clean = re.sub(r"<.*?>", " ", tooltip)

            summary_match = re.match(r"\s*([A-Za-z ]+)", clean)
            percent_match = re.search(r"(\d+)%", clean)
            count_match = re.search(r"([\d,]+)\s+user reviews", clean)

            if summary_match:
                review_summary = summary_match.group(1).strip()

            if percent_match:
                review_percent = int(percent_match.group(1))

            if count_match:
                review_count = int(count_match.group(1).replace(",", ""))

        image_url = ""

        games.append({
            "appid": appid,
            "title": title,
            "discount": discount,
            "original_price": original_price,
            "final_price": final_price,
            "final_price_php": final_price_php,
            "review_summary": review_summary,
            "review_percent": review_percent,
            "review_count": review_count,
            "release_date": release_date,
            "image_url": image_url,
            "url": url,
        })

    return games


def fetch_live_sale_pool(total=500):
    all_games = []
    count = 100

    for start in range(0, total, count):
        print(f"Fetching Steam deals {start}-{start + count}")

        games = fetch_steam_deals(start=start, count=count)

        if not games:
            break

        all_games.extend(games)

    seen = set()
    unique_games = []

    for game in all_games:
        if game["appid"] not in seen:
            seen.add(game["appid"])
            unique_games.append(game)

    return unique_games[:total]


def fetch_appdetails(appid):
    params = {
        "appids": appid,
        "cc": COUNTRY,
        "l": LANGUAGE,
    }

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(APPDETAILS_URL, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        app_data = data.get(str(appid), {})

        if not app_data.get("success"):
            return {}

        return app_data.get("data", {})

    except Exception as e:
        print(f"Failed to fetch appdetails for {appid}: {e}")
        return {}


def enrich_with_appdetails(game):
    appid = game["appid"]

    game["image_url"] = build_steam_library_capsule_url(appid, COUNTRY)

    details = fetch_appdetails(appid)

    screenshots = details.get("screenshots", [])

    screenshot_urls = [
        screenshot.get("path_full", "")
        for screenshot in screenshots
        if screenshot.get("path_full")
    ]

    game["screenshot_1"] = screenshot_urls[0] if len(screenshot_urls) > 0 else ""
    game["screenshot_2"] = screenshot_urls[1] if len(screenshot_urls) > 1 else ""
    game["screenshot_3"] = screenshot_urls[2] if len(screenshot_urls) > 2 else ""
    game["screenshot_4"] = screenshot_urls[3] if len(screenshot_urls) > 3 else ""
    game["screenshot_5"] = screenshot_urls[4] if len(screenshot_urls) > 4 else ""

    game["header_image"] = details.get("header_image", "")
    game["background_raw"] = details.get("background_raw", "")
    game["short_description"] = details.get("short_description", "")

    return game


def blank_itad_fields(game):
    game["itad_id"] = ""
    game["expiration_date"] = ""
    game["historic_low_all"] = ""
    game["historic_low_1y"] = ""
    game["historic_low_3m"] = ""
    game["store_low"] = ""
    game["new_historic_low"] = "N"

    return game


def get_amount(value):
    if not isinstance(value, dict):
        return ""

    amount = value.get("amount")
    return "" if amount is None else amount


def get_deal_expiry(price_row):
    deals = price_row.get("deals", [])

    if not deals:
        return ""

    # You are requesting Steam only with shops=61, but keep this defensive.
    steam_deal = next(
        (
            deal for deal in deals
            if str(deal.get("shop", {}).get("id", "")) == "61"
        ),
        deals[0],
    )

    return steam_deal.get("expiry") or ""

def get_store_low(price_row):
    deals = price_row.get("deals", [])

    if not deals:
        return ""

    steam_deal = next(
        (
            deal for deal in deals
            if str(deal.get("shop", {}).get("id", "")) == "61"
        ),
        deals[0],
    )

    store_low = steam_deal.get("storeLow", {})

    return get_amount(store_low)

def get_deal_timestamp(price_row):
    deals = price_row.get("deals", [])

    if not deals:
        return ""

    steam_deal = next(
        (
            deal for deal in deals
            if str(deal.get("shop", {}).get("id", "")) == "61"
        ),
        deals[0],
    )

    return steam_deal.get("timestamp") or ""

def fetch_itad_id_for_appid(appid):
    params = {
        "appid": appid,
        "key": ITAD_API_KEY,
    }

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(
            ITAD_LOOKUP_URL,
            params=params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()

        if not data.get("found"):
            return {
                "found": False,
                "itad_id": "",
                "title": "",
                "slug": "",
            }

        game = data.get("game", {}) or {}

        return {
            "found": True,
            "itad_id": game.get("id", ""),
            "title": game.get("title", ""),
            "slug": game.get("slug", ""),
        }

    except Exception as e:
        print(f"[ITAD] lookup failed for appid {appid}: {e}")
        return {
            "found": False,
            "itad_id": "",
            "title": "",
            "slug": "",
        }


def normalize_itad_cache_entry(entry):
    """
    Supports both the new dict cache format and a simple old format:
    { "123": "itad-id" }
    """
    if isinstance(entry, str):
        return {
            "found": bool(entry),
            "itad_id": entry,
            "title": "",
            "slug": "",
        }

    if isinstance(entry, dict):
        return {
            "found": bool(entry.get("found")),
            "itad_id": entry.get("itad_id") or entry.get("id") or "",
            "title": entry.get("title", ""),
            "slug": entry.get("slug", ""),
        }

    return {
        "found": False,
        "itad_id": "",
        "title": "",
        "slug": "",
    }


def get_itad_mappings_for_games(games):
    cache = load_json(ITAD_CACHE_FILE, {})
    updated_cache = False
    mappings = {}

    for game in games:
        appid = str(game.get("appid", "")).strip()

        if not appid:
            continue

        if appid in cache:
            cache_entry = normalize_itad_cache_entry(cache[appid])
            print(f"[ITAD] cache hit: {appid} -> {cache_entry.get('itad_id') or 'not found'}")
        else:
            cache_entry = fetch_itad_id_for_appid(appid)
            cache[appid] = cache_entry
            updated_cache = True
            print(f"[ITAD] lookup: {appid} -> {cache_entry.get('itad_id') or 'not found'}")

        if cache_entry.get("found") and cache_entry.get("itad_id"):
            mappings[appid] = cache_entry["itad_id"]

    if updated_cache:
        save_json(ITAD_CACHE_FILE, cache)
        print(f"[ITAD] saved cache: {ITAD_CACHE_FILE}")

    return mappings


def fetch_itad_prices(itad_ids):
    if not itad_ids:
        return {}

    params = {
        "country": COUNTRY,
        "shops": 61,
        "key": ITAD_API_KEY,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        response = requests.post(
            ITAD_PRICES_URL,
            params=params,
            headers=headers,
            json=itad_ids,
            timeout=60,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            print("[ITAD] unexpected prices response format")
            return {}

        return {
            item.get("id"): item
            for item in data
            if isinstance(item, dict) and item.get("id")
        }

    except Exception as e:
        print(f"[ITAD] prices lookup failed: {e}")
        return {}

def fetch_itad_store_lows(itad_ids):
    if not itad_ids:
        return {}

    params = {
        "country": COUNTRY,
        "shops": 61,
        "key": ITAD_API_KEY,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        response = requests.post(
            ITAD_STORELOW_URL,
            params=params,
            headers=headers,
            json=itad_ids,
            timeout=60,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            print("[ITAD] unexpected storelow response format")
            return {}

        return {
            item.get("id"): item
            for item in data
            if isinstance(item, dict) and item.get("id")
        }

    except Exception as e:
        print(f"[ITAD] storelow lookup failed: {e}")
        return {}


def get_current_sale_amount_int(game):
    value = game.get("final_price_php")

    if value in ("", None):
        return None

    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def is_new_historic_low_from_storelow(
    storelow_row,
    current_sale_amount_int,
    sale_start_timestamp,
    tolerance_days=1,
):
    lows = storelow_row.get("lows", [])

    if not lows or current_sale_amount_int is None or not sale_start_timestamp:
        return False

    steam_low = next(
        (
            low for low in lows
            if str(low.get("shop", {}).get("id", "")) == "61"
        ),
        lows[0],
    )

    low_price_int = steam_low.get("price", {}).get("amountInt")
    storelow_timestamp = steam_low.get("timestamp")

    if low_price_int is None or not storelow_timestamp:
        return False

    # Current Steam sale price must match the Steam store-low price.
    if int(current_sale_amount_int) != int(low_price_int):
        return False

    try:
        sale_start_dt = datetime.fromisoformat(sale_start_timestamp)
        storelow_dt = datetime.fromisoformat(storelow_timestamp)
    except ValueError:
        return False

    # New historic low means the store-low record was created during this Steam sale.
    # The tolerance handles small timezone/API timing differences.
    return storelow_dt >= sale_start_dt - timedelta(days=tolerance_days)


def enrich_with_itad(games):
    """
    Adds ITAD price-history data only for the already-selected daily target.
    Performs one cached appid -> ITAD ID lookup pass, then one batch prices request.
    """
    for game in games:
        blank_itad_fields(game)

    appid_to_itad_id = get_itad_mappings_for_games(games)
    itad_ids = list(dict.fromkeys(appid_to_itad_id.values()))

    print(f"[ITAD] fetching prices for {len(itad_ids)} mapped daily games")
    prices_by_itad_id = fetch_itad_prices(itad_ids)

    print(f"[ITAD] fetching store lows for {len(itad_ids)} mapped daily games")
    store_lows_by_itad_id = fetch_itad_store_lows(itad_ids)

    for game in games:
        appid = str(game.get("appid", "")).strip()
        itad_id = appid_to_itad_id.get(appid, "")

        if not itad_id:
            continue

        game["itad_id"] = itad_id

        price_row = prices_by_itad_id.get(itad_id, {})
        history_low = price_row.get("historyLow", {}) if isinstance(price_row, dict) else {}

        game["historic_low_all"] = get_amount(history_low.get("all"))
        game["historic_low_1y"] = get_amount(history_low.get("y1"))
        game["historic_low_3m"] = get_amount(history_low.get("m3"))
        game["store_low"] = get_store_low(price_row)
        game["expiration_date"] = get_deal_expiry(price_row)

        storelow_row = store_lows_by_itad_id.get(itad_id, {})
        current_sale_amount_int = get_current_sale_amount_int(game)
        sale_start_timestamp = get_deal_timestamp(price_row)

        game["new_historic_low"] = (
            "Y"
            if is_new_historic_low_from_storelow(
                storelow_row,
                current_sale_amount_int,
                sale_start_timestamp,
                tolerance_days=1,
            )
            else "N"
        )

    return games


def load_json(path, fallback):
    if not os.path.exists(path):
        return fallback

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_recently_posted():
    posted = load_json(POSTED_FILE, {
        "posted": {}
    })

    posted_map = posted.get("posted", {})

    cutoff = datetime.now().date() - timedelta(days=ROLLING_DAYS)

    cleaned_posted = {}

    for appid, date_str in posted_map.items():
        try:
            posted_date = datetime.fromisoformat(date_str).date()
        except ValueError:
            continue

        if posted_date > cutoff:
            cleaned_posted[appid] = date_str

    posted["posted"] = cleaned_posted

    return posted


def build_daily_batch():
    posted = load_recently_posted()
    posted_appids = set(posted["posted"].keys())

    hltb_lookup = load_hltb_dataset()

    live_games = fetch_live_sale_pool(FETCH_LIMIT)

    daily_batch = [
        game for game in live_games
        if game["appid"] not in posted_appids
    ][:DAILY_TARGET]

    daily_batch = enrich_with_itad(daily_batch)
    daily_batch = enrich_with_steam_deck_compatibility(daily_batch)

    today = today_str()

    enriched_batch = []

    for index, game in enumerate(daily_batch, start=1):
        print(f"Enriching {index}/{len(daily_batch)}: {game['title']}")

        enriched_game = enrich_with_appdetails(game)
        enriched_game = enrich_with_hltb(enriched_game, hltb_lookup)

        enriched_batch.append(enriched_game)

        posted["posted"][game["appid"]] = today

    save_json(POSTED_FILE, posted)

    return enriched_batch


def export_csv(games, filename=OUTPUT_CSV):
    fields = [
        "appid",
        "title",
        "discount",
        "original_price",
        "final_price",
        "final_price_php",
        "expiration_date",
        "historic_low_all",
        "historic_low_1y",
        "historic_low_3m",
        "store_low",
        "new_historic_low",
        "itad_id",
        "steam_deck_status",
        "steam_deck_category",
        "steam_deck_tested",
        "review_summary",
        "review_percent",
        "review_count",
        "release_date",
        "image_url",
        "screenshot_1",
        "screenshot_2",
        "screenshot_3",
        "screenshot_4",
        "screenshot_5",
        "header_image",
        "background_raw",
        "short_description",
        "MainStory",
        "MainExtra",
        "Completionist",
        "url",
    ]

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for game in games:
            writer.writerow({
                field: game.get(field, "")
                for field in fields
            })


def main():
    games = build_daily_batch()
    export_csv(games)

    print()
    print("=" * 50)
    print(f"Saved {len(games)} games")
    print(f"CSV: {OUTPUT_CSV}")
    print(f"Rolling no-repeat window: {ROLLING_DAYS} days")
    print("=" * 50)

    for game in games[:10]:
        print(
            f"{game['title']} | "
            f"-{game['discount']}% | "
            f"{game['final_price']}"
        )


if __name__ == "__main__":
    main()
