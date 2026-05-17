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

COUNTRY = "PH"
LANGUAGE = "english"

POSTED_FILE = "steam_posted_recently.json"
OUTPUT_CSV = "steam_deals_today.csv"
HLTB_DATASET_CSV = "hltb_dataset_filtered.csv"

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

        discount = 0
        final_cents = 0

        if discount_block:
            discount = int(discount_block.get("data-discount", 0))
            final_cents = int(discount_block.get("data-price-final", 0))

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
