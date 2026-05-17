# facebookPostSteam.py
import os
import requests
from datetime import datetime, timedelta, timezone
import time
from pathlib import Path
import json

# ===== FACEBOOK CONFIG =====
PAGE_ID = "110345971129305"

# LIVE TOKEN
ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN_LIVE")
#ACCESS_TOKEN = "EAAQQDe5YQC4BPJPPsPMXxsPYJ3gblJs3SDsCYG25QTlhIgMj2lrjR6X9VP9kYxdX1PI3Ty01FNsviagC0UbxwvISknFb870L3NUZBQ6bL1EXR7YOT0ndsMfO2z1aNZAK2V8BVgKlrZAfuQZBDCMIVn2yRkcPdG4w8eR4HaReV4CUShxMHubyZBQoSAqGOngZCvAeA9lvTisnuEqSiiRlmq6TsZD"

# TEST TOKEN OPTION
# ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN_TEST")

if not ACCESS_TOKEN:
    print("❌ Missing Facebook access token.")
    print("Set FB_PAGE_ACCESS_TOKEN_LIVE in your environment.")
    raise SystemExit(1)

# ===== STEAM DEALS CONFIG =====
IMAGE_FOLDER = Path("output_images")
POST_TITLE = "Popular Steam Games On Sale"

COMMENTS = [
    "🎮 Cheap Steam Wallet: https://www.eneba.com/steam-gift-card-steam-wallet-gift-card-50-php-steam-key-philippines?af_id=maysaleba&currency=PHP&region=philippines"
]

MAX_IMAGES_PER_POST = None
# Example: set to 10 if you only want first 10 images
# MAX_IMAGES_PER_POST = 10

# ===== READ IMAGE FILES =====
if not IMAGE_FOLDER.is_dir():
    print(f"❌ Folder not found: {IMAGE_FOLDER}")
    raise SystemExit(1)

image_files = [
    path for path in sorted(IMAGE_FOLDER.iterdir())
    if path.suffix.lower() in [".jpg", ".jpeg", ".png"]
]

if MAX_IMAGES_PER_POST:
    image_files = image_files[:MAX_IMAGES_PER_POST]

if not image_files:
    print("⚠️ No image files found.")
    raise SystemExit(0)

print(f"🖼️ Found {len(image_files)} images in {IMAGE_FOLDER}")

# ===== UPLOAD IMAGES AS UNPUBLISHED =====
uploaded_media = []

for path in image_files:
    print(f"📤 Uploading: {path}")

    upload_url = f"https://graph.facebook.com/v23.0/{PAGE_ID}/photos"

    with open(path, "rb") as img_file:
        response = requests.post(
            upload_url,
            files={"source": img_file},
            data={
                "published": "false",
                "access_token": ACCESS_TOKEN
            },
            timeout=60
        )

    try:
        result = response.json()
    except Exception:
        print(f"❌ Upload failed, non-JSON response: {response.text}")
        continue

    if "id" in result:
        media_id = result["id"]
        uploaded_media.append({"media_fbid": media_id})
        print(f"✅ Uploaded: {media_id}")
    else:
        print(f"❌ Upload failed: {result}")

if not uploaded_media:
    print("⚠️ No images uploaded successfully.")
    raise SystemExit(0)

# ===== PUBLISH FACEBOOK POST =====
PH_TZ = timezone(timedelta(hours=8))
date_text = datetime.now(PH_TZ).strftime("%b %d %Y")

post_message = f"{POST_TITLE} - {date_text}"

print(f"\n📝 Publishing post with {len(uploaded_media)} images...")

post_url = f"https://graph.facebook.com/v23.0/{PAGE_ID}/feed"

payload = {
    "message": post_message,
    "access_token": ACCESS_TOKEN,
    "attached_media": json.dumps(uploaded_media)
}

response = requests.post(post_url, data=payload, timeout=60)

try:
    res_json = response.json()
except Exception:
    print(f"❌ Failed to publish post, non-JSON response: {response.text}")
    raise SystemExit(1)

if "id" not in res_json:
    print(f"❌ Failed to publish post:\n{res_json}")
    raise SystemExit(1)

post_id = res_json["id"]
print(f"✅ Post published: {post_id}")

# ===== ADD COMMENTS =====
for text in COMMENTS:
    if not text.strip():
        continue

    comment_url = f"https://graph.facebook.com/v23.0/{post_id}/comments"

    comment_payload = {
        "message": text,
        "access_token": ACCESS_TOKEN
    }

    comment_response = requests.post(
        comment_url,
        data=comment_payload,
        timeout=60
    )

    try:
        result = comment_response.json()
    except Exception:
        print(f"❌ Comment failed, non-JSON response: {comment_response.text}")
        continue

    if "id" in result:
        print(f"💬 Comment added: {result['id']}")
    else:
        print(f"❌ Failed to add comment: {result}")

    time.sleep(2)

print("✅ Done.")
