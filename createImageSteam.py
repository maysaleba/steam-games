import csv
import re
import random
import unicodedata
import requests
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ====== CONFIG ======
INPUT_CSV    = Path("steam_deals_today.csv")
OVERLAY_PATH = Path("steam-template.png")
SAVE_DIR     = Path("output_images")
CANVAS_SIZE  = 1000

# ====== FONTS ======
FONT_PATH        = Path("Montserrat-Bold.ttf")
TEXT_FONT_PATH   = Path("Montserrat-Bold.ttf")
STRIKE_FONT_PATH = Path("Montserrat-Bold.ttf")

# ====== STEAM LOGO ======
STEAM_LOGO_PATH = Path("logos/steam-logo.png")
LOGO_POS = (15, 15)
LOGO_SIZE = (96, 96)

# ====== ADS ======
ADS_DIR  = Path("ads")
AD_SLOTS = {5, 6, 7, 12, 13, 14, 19, 20, 21, 26, 27, 28, 33, 34, 35, 40, 41, 42}

# ====== MAIN IMAGE LAYOUT ======
MAIN_IMG_W = 598
MAIN_IMG_H = 900
MAIN_IMG_POS = (0, 0)

# ====== SCREENSHOT LAYOUT ======
SCREENSHOT_X = 598
SCREENSHOT_W = 402
SCREENSHOT_H = 333
SCREENSHOT_POSITIONS = [
    (SCREENSHOT_X, 0),
    (SCREENSHOT_X, 333),
    (SCREENSHOT_X, 666),
]

# ====== DISCOUNT TEXT ======
BOX_X, BOX_Y = 12, CANVAS_SIZE - 135
BOX_W, BOX_H = 300, 140
PAD_X, PAD_Y = 8, 12

# ====== SALE PRICE ======
PRICE_X = 220
PRICE_Y = 933
TEXT_PT_SIZE = 46
TEXT_FILL = "#b9e919"
STROKE_FILL = (0, 0, 0, 180)
STROKE_WIDTH = 0

# ====== ORIGINAL PRICE ======
STRIKE_X = 220
STRIKE_Y = 908
STRIKE_FONT_SIZE = 20
STRIKE_COLOR = (120, 120, 120)
STRIKE_LINE_WIDTH = 2

# ====== HISTORIC LOW BADGE ======
ALL_TIME_LOW_BADGE_PATH = Path("logos/all-time-low.png")
ALL_TIME_LOW_BADGE_GAP = 8
ALL_TIME_LOW_BADGE_H = 19

# ====== SCORE BADGE ======
SCORE_BADGE_PATH = Path("logos/score.png")
SCORE_TOP_OFFSET = 27
SCORE_RIGHT_OFFSET = 420
SCORE_FONT_PATH = Path("Bahnschrift-Bold.ttf")
SCORE_FONT_SIZE = 28
SCORE_TEXT_FILL = "white"

# ==== HOWLONGTOBEAT ICON ====
HLTB_ICON_PATH = Path("howlongtobeat.png")
HLTB_POS = (15, 698)
HLTB_SIZE = (265, 186)
HLTB_KEYS = ["MainStory", "MainExtra", "Completionist"]

# ==== HLTB TIMES TEXT (fixed positions) ====
HLTB_TEXT_FONT_PATH = Path("CoreSansDS67CnHeavy.ttf")
HLTB_TEXT_FONT_SIZE = 24
HLTB_TEXT_COLOR = "white"
HLTB_TEXT_POS = {
    "MainStory": (172, 766),
    "MainExtra": (172, 799),
    "Completionist": (172, 832),
}

SAVE_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r'[^A-Za-z0-9._ -]+', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name or "untitled"


def parse_price(value):
    if value is None:
        return None

    s = str(value).strip()

    if not s or s.lower() in {"nan", "none", "null", "free to play"}:
        return None

    s = s.replace("₱", "")
    s = s.replace("PHP", "")
    s = s.replace("P", "")
    s = s.replace(",", "")
    s = s.strip()

    try:
        return float(s)
    except Exception:
        return None


def format_php(value):
    price = parse_price(value)

    if price is None:
        return "FREE"

    if price <= 0:
        return "FREE"

    return f"P{price:,.2f}"


def fetch_image(url: str) -> Image.Image:
    headers = {"User-Agent": "Mozilla/5.0"}

    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()

    return Image.open(BytesIO(r.content)).convert("RGB")


def cover_resize_center_crop_to_size(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    w, h = img.size
    scale = max(target_w / w, target_h / h)

    new_w = int(w * scale)
    new_h = int(h * scale)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2

    return img.crop((left, top, left + target_w, top + target_h)).convert("RGBA")


def cover_resize_center_crop(img: Image.Image, size: int) -> Image.Image:
    return cover_resize_center_crop_to_size(img, size, size).convert("RGB")


def make_base_layer(src: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 255))

    main_img = cover_resize_center_crop_to_size(src, MAIN_IMG_W, MAIN_IMG_H)
    canvas.alpha_composite(main_img, MAIN_IMG_POS)

    return canvas


def get_screenshot_urls(entry: dict):
    screenshots = []

    for key, value in entry.items():
        if not key.startswith("screenshot_"):
            continue

        if not value:
            continue

        url = str(value).strip()

        if url.startswith("http"):
            screenshots.append(url)

    return screenshots


def select_screenshots(screenshot_urls, title: str = ""):
    if not screenshot_urls:
        return []

    shuffled = screenshot_urls[:]
    random.shuffle(shuffled)

    return shuffled[:3]


def draw_screenshots(img_rgba: Image.Image, entry: dict):
    title = entry.get("title", "")
    screenshot_urls = get_screenshot_urls(entry)
    selected = select_screenshots(screenshot_urls, title)

    if not selected:
        return

    for url, pos in zip(selected, SCREENSHOT_POSITIONS):
        try:
            shot = fetch_image(url)
            shot = cover_resize_center_crop_to_size(shot, SCREENSHOT_W, SCREENSHOT_H)
            img_rgba.alpha_composite(shot, pos)

        except Exception as e:
            print(f"[warn] Could not draw screenshot {url}: {e}")


def load_font_to_fit(text: str, max_w: int, max_h: int, start_size: int = 65):
    size = start_size

    while size > 10:
        font = ImageFont.truetype(str(FONT_PATH), size)
        bbox = font.getbbox(text)

        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        if tw <= max_w and th <= max_h:
            return font

        size -= 2

    return ImageFont.truetype(str(FONT_PATH), 10)


def draw_platform_logo(img_rgba: Image.Image):
    if not STEAM_LOGO_PATH.exists():
        print(f"[warn] Missing steam logo: {STEAM_LOGO_PATH}")
        return

    try:
        logo = Image.open(STEAM_LOGO_PATH).convert("RGBA")

        if logo.size != LOGO_SIZE:
            logo = logo.resize(LOGO_SIZE, Image.LANCZOS)

        img_rgba.alpha_composite(logo, LOGO_POS)

    except Exception as e:
        print(f"[warn] Could not load steam logo: {e}")


def draw_discount(img_rgba: Image.Image, discount_text: str):
    if not discount_text:
        return

    discount_text = str(discount_text).strip()

    discount_text = discount_text.replace("%", "").strip()

    if discount_text:
        discount_text = f"-{discount_text}%"

    draw = ImageDraw.Draw(img_rgba)
    font = load_font_to_fit(
        discount_text,
        BOX_W - 2 * PAD_X,
        BOX_H - 2 * PAD_Y
    )

    bbox = font.getbbox(discount_text)
    th = bbox[3] - bbox[1]

    x = BOX_X + PAD_X
    y = BOX_Y + (BOX_H - th) // 2

    draw.text((x, y), discount_text, font=font, fill="#bcec0c")


def draw_sale_price(img_rgba: Image.Image, final_price_php):
    font = ImageFont.truetype(str(TEXT_FONT_PATH), TEXT_PT_SIZE)
    draw = ImageDraw.Draw(img_rgba)

    text = format_php(final_price_php)

    draw.text(
        (PRICE_X, PRICE_Y),
        text,
        font=font,
        fill=TEXT_FILL,
        stroke_width=STROKE_WIDTH,
        stroke_fill=STROKE_FILL,
    )


def draw_struck_price(img_rgba: Image.Image, original_price):
    price = parse_price(original_price)

    if price is None or price <= 0:
        return

    text = f"P{int(round(price)):,}"

    font = ImageFont.truetype(str(STRIKE_FONT_PATH), STRIKE_FONT_SIZE)
    draw = ImageDraw.Draw(img_rgba)

    draw.text((STRIKE_X, STRIKE_Y), text, font=font, fill=STRIKE_COLOR)

    bbox = font.getbbox(text)
    text_w = bbox[2] - bbox[0]

    ascent, _ = font.getmetrics()
    line_y = STRIKE_Y + int(ascent * 0.6)

    draw.line(
        [(STRIKE_X, line_y), (STRIKE_X + text_w, line_y)],
        fill=STRIKE_COLOR,
        width=STRIKE_LINE_WIDTH,
    )


def get_struck_price_right_edge(original_price):
    price = parse_price(original_price)

    if price is None or price <= 0:
        return STRIKE_X

    text = f"P{int(round(price)):,}"
    font = ImageFont.truetype(str(STRIKE_FONT_PATH), STRIKE_FONT_SIZE)
    bbox = font.getbbox(text)

    return STRIKE_X + (bbox[2] - bbox[0])


def should_show_all_time_low_badge(final_price_php, historic_low_all):
    current_price = parse_price(final_price_php)
    historic_low = parse_price(historic_low_all)

    if current_price is None or historic_low is None:
        return False

    return current_price <= historic_low


def draw_all_time_low_badge(img_rgba: Image.Image, entry: dict):
    if not should_show_all_time_low_badge(
        entry.get("final_price_php"),
        entry.get("historic_low_all"),
    ):
        return

    if not ALL_TIME_LOW_BADGE_PATH.exists():
        print(f"[warn] Missing all-time low badge: {ALL_TIME_LOW_BADGE_PATH}")
        return

    try:
        badge = Image.open(ALL_TIME_LOW_BADGE_PATH).convert("RGBA")

        if ALL_TIME_LOW_BADGE_H and badge.height != ALL_TIME_LOW_BADGE_H:
            ratio = ALL_TIME_LOW_BADGE_H / badge.height
            badge_w = int(badge.width * ratio)
            badge = badge.resize((badge_w, ALL_TIME_LOW_BADGE_H), Image.LANCZOS)

        strike_right = get_struck_price_right_edge(entry.get("original_price"))
        x = strike_right + ALL_TIME_LOW_BADGE_GAP
        y = STRIKE_Y + (STRIKE_FONT_SIZE - badge.height) // 2 + 3

        # Keep badge inside the canvas if the original price is unusually long.
        x = min(x, img_rgba.width - badge.width - 8)
        x = max(x, 0)
        y = max(y, 0)

        img_rgba.alpha_composite(badge, (x, y))

    except Exception as e:
        print(f"[warn] Could not draw all-time low badge: {e}")


def draw_score_badge(img_rgba: Image.Image, review_percent):
    if not review_percent:
        return

    try:
        score = int(float(str(review_percent).strip()))
    except Exception:
        return

    if score <= 0:
        return

    if not SCORE_BADGE_PATH.exists():
        return

    try:
        badge = Image.open(SCORE_BADGE_PATH).convert("RGBA")
        badge_w, badge_h = badge.size

        x = img_rgba.width - SCORE_RIGHT_OFFSET - badge_w
        y = SCORE_TOP_OFFSET

        img_rgba.alpha_composite(badge, (x, y))

        font = ImageFont.truetype(str(SCORE_FONT_PATH), SCORE_FONT_SIZE)
        text = str(score)

        draw = ImageDraw.Draw(img_rgba)
        bbox = font.getbbox(text)

        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        tx = x + (badge_w - tw) // 2
        ty = y + (badge_h - th) // 2 - 2

        draw.text((tx, ty), text, font=font, fill=SCORE_TEXT_FILL)

    except Exception as e:
        print(f"[warn] Could not draw score badge: {e}")



def has_hltb_data(entry: dict) -> bool:
    """Return True if any HLTB value is numeric and greater than 0."""
    for key in HLTB_KEYS:
        try:
            value = float(str(entry.get(key, "")).strip())
            if value > 0:
                return True
        except Exception:
            pass

    return False


def draw_hltb_icon(img_rgba: Image.Image, entry: dict):
    """Draw the HowLongToBeat panel only when HLTB data exists."""
    if not has_hltb_data(entry):
        return

    if not HLTB_ICON_PATH.exists():
        print(f"[warn] Missing HLTB icon: {HLTB_ICON_PATH}")
        return

    try:
        icon = Image.open(HLTB_ICON_PATH).convert("RGBA")

        if icon.size != HLTB_SIZE:
            icon = icon.resize(HLTB_SIZE, Image.LANCZOS)

        img_rgba.alpha_composite(icon, HLTB_POS)

    except Exception as e:
        print(f"[warn] Could not draw HLTB icon: {e}")


def format_hltb_value(value) -> str:
    """Return '-' for missing/zero values, otherwise '<number> HRS'."""
    try:
        hours = float(str(value).strip())
    except Exception:
        return "-"

    if hours <= 0:
        return "-"

    if hours.is_integer():
        return f"{int(hours)} HRS"

    # Keeps half-hour values readable, e.g. 7.5 HRS.
    return f"{hours:g} HRS"


def draw_hltb_times(img_rgba: Image.Image, entry: dict):
    """Draw MainStory, MainExtra, and Completionist values at fixed positions."""
    if not has_hltb_data(entry):
        return

    if not HLTB_TEXT_FONT_PATH.exists():
        print(f"[warn] Missing HLTB text font: {HLTB_TEXT_FONT_PATH}")
        return

    try:
        font = ImageFont.truetype(str(HLTB_TEXT_FONT_PATH), HLTB_TEXT_FONT_SIZE)
    except Exception as e:
        print(f"[warn] Could not load HLTB text font: {e}")
        return

    draw = ImageDraw.Draw(img_rgba)

    for key, (x, y) in HLTB_TEXT_POS.items():
        text = format_hltb_value(entry.get(key))
        draw.text((x, y), text, font=font, fill=HLTB_TEXT_COLOR)


def cover_resize_center_crop_rgb(img_path: Path, size: int) -> Image.Image:
    im = Image.open(img_path).convert("RGB")
    return cover_resize_center_crop(im, size)


def main():
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Cannot find {INPUT_CSV}")

    if not OVERLAY_PATH.exists():
        raise FileNotFoundError(f"Cannot find overlay image {OVERLAY_PATH}")

    required_fonts = [
        FONT_PATH,
        TEXT_FONT_PATH,
        STRIKE_FONT_PATH,
        SCORE_FONT_PATH,
        HLTB_TEXT_FONT_PATH,
    ]

    for font_path in required_fonts:
        if not font_path.exists():
            raise FileNotFoundError(f"Cannot find font file: {font_path}")

    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        data = list(csv.DictReader(f))

    overlay_rgba = Image.open(OVERLAY_PATH).convert("RGBA")

    if overlay_rgba.size != (CANVAS_SIZE, CANVAS_SIZE):
        overlay_rgba = overlay_rgba.resize((CANVAS_SIZE, CANVAS_SIZE), Image.LANCZOS)

    ad_paths = []

    if ADS_DIR.exists():
        ad_paths = [
            p for p in sorted(ADS_DIR.iterdir())
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]

    ad_i = 0
    out_idx = 1
    data_i = 0

    while data_i < len(data):
        if out_idx in AD_SLOTS:
            if not ad_paths:
                print(f"[{out_idx}] Skipped ad slot — no ads found.")
                out_idx += 1
                continue

            ad_path = ad_paths[ad_i % len(ad_paths)]

            try:
                ad_img = cover_resize_center_crop_rgb(ad_path, CANVAS_SIZE)
                out_path = SAVE_DIR / f"{out_idx:02d}_ad-{ad_i + 1:02d}.jpg"

                ad_img.save(out_path, format="JPEG", quality=92, optimize=True)

                print(f"[{out_idx}] Inserted AD: {out_path.name}")

                ad_i += 1

            except Exception as e:
                print(f"[{out_idx}] Failed to insert ad '{ad_path}': {e}")

            out_idx += 1
            continue

        entry = data[data_i]

        title = entry.get("title") or f"item_{data_i + 1}"
        img_url = entry.get("image_url")
        discount = entry.get("discount")
        final_price_php = entry.get("final_price_php")
        original_price = entry.get("original_price")
        review_percent = entry.get("review_percent")

        if not img_url:
            print(f"[{out_idx}] Skipped '{title}' — no image_url.")
            data_i += 1
            out_idx += 1
            continue

        try:
            src = fetch_image(img_url)

            base_layer = make_base_layer(src)
            draw_screenshots(base_layer, entry)

            composed = Image.alpha_composite(base_layer, overlay_rgba)

            draw_platform_logo(composed)
            draw_score_badge(composed, review_percent)
            draw_hltb_icon(composed, entry)
            draw_discount(composed, discount)
            draw_struck_price(composed, original_price)
            draw_all_time_low_badge(composed, entry)
            draw_sale_price(composed, final_price_php)
            draw_hltb_times(composed, entry)

            safe_title = sanitize_filename(title)
            out_path = SAVE_DIR / f"{out_idx:02d}_{safe_title}.jpg"

            composed.convert("RGB").save(
                out_path,
                format="JPEG",
                quality=92,
                optimize=True
            )

            print(f"[{out_idx}] Saved: {out_path}")

        except Exception as e:
            print(f"[{out_idx}] Failed '{title}': {e}")

        data_i += 1
        out_idx += 1


if __name__ == "__main__":
    main()
