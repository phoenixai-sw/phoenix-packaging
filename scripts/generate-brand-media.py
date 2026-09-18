"""Generate the site's design media and the editor's sample backgrounds with GPT Image 2.5.

The API key is read from a local file path given on the command line at run time only;
it is never stored in the repository, logged or embedded in the output. Prompts, sizes,
usage and estimated cost are recorded next to the images so the media stays traceable.

Usage:
  python scripts/generate-brand-media.py --key-file "<path>" --set site
  python scripts/generate-brand-media.py --key-file "<path>" --set backgrounds
"""
import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-image-2.5-sunburst"
QUALITY = "xhigh"
STYLE = ("Joyful, bright and modern Korean food-brand aesthetic: fresh saturated but harmonious colours, soft natural light, "
         "friendly rounded illustration touches, clean premium finish, nothing dull or beige-heavy. ")
NO_TEXT = ("Absolutely no letters, words, numbers, logos, barcodes or watermarks anywhere. ")

SITE = [
    {"out": "apps/web/public/media/packaging-hero.webp", "size": "1536x1024",
     "prompt": "Use case: product-mockup. Editorial campaign photograph for a Korean packaging design studio. " + STYLE +
               "A cheerful still life of four flexible stand-up food pouches on a sunlit pastel-yellow and mint tabletop: a warm orange duck jerky snack pouch "
               "with a cute minimalist duck illustration, a Jeju tangerine pouch in bright citrus orange with green leaves, a berry granola pouch in pink and "
               "cream, and a matcha pouch in fresh spring green. Each pouch has beautiful, legible Korean and English typography and a small round hang hole "
               "at the top. Fresh fruit, leaves and a few snacks scattered playfully, soft shadows, high-end product photography, 35mm, crisp detail."},
    {"out": "apps/web/public/media/concept-duck.webp", "size": "1024x1024",
     "prompt": "Use case: product-mockup. High-end product photograph of one bright stand-up pouch for a Korean duck jerky snack for dogs and people, "
               + STYLE + "Warm tangerine-orange pouch with a cute friendly duck character illustration in a cream circle, playful Korean title typography, "
               "a small round hang hole at the top, matte finish, standing on a sunny cream studio surface with soft coral shadow, a few jerky sticks beside it."},
    {"out": "apps/web/public/media/concept-citrus.webp", "size": "1024x1024",
     "prompt": "Use case: product-mockup. High-end product photograph of one three-side-sealed flat pouch for Jeju tangerine chips, " + STYLE +
               "Vivid citrus orange and fresh green colour blocking, hand-drawn tangerine slices and leaves, clean modern Korean typography, "
               "lying at a slight angle on a sky-blue studio surface with real tangerines and leaves, bright daylight, crisp detail."},
    {"out": "apps/web/public/media/concept-berry.webp", "size": "1024x1024",
     "prompt": "Use case: product-mockup. High-end product photograph of one zipper stand-up pouch for berry granola, " + STYLE +
               "Raspberry pink and soft cream palette with a cheerful pattern of berries and oats, elegant rounded Korean typography, a transparent "
               "window showing granola, standing on a pale pink surface with scattered berries and a small bowl of yogurt, airy morning light."},
    {"out": "apps/web/public/media/concept-matcha.webp", "size": "1024x1024",
     "prompt": "Use case: product-mockup. High-end product photograph of one matte stand-up pouch for premium matcha latte powder, " + STYLE +
               "Fresh spring-green and milky-white palette, a modern minimal leaf illustration, refined Korean typography, standing on a light wood tray "
               "with a ceramic bowl of matcha and a whisk, bright cafe daylight, crisp detail."},
]

BACKGROUNDS = [
    ("forest", "Fresh matcha green watercolour wash with soft white light leaks and a few loose hand-painted tea leaves near the bottom edge; keep the upper half calm and almost plain for a product title."),
    ("citrus", "Bright tangerine-orange gradient with playful hand-drawn citrus slices, leaves and tiny dots scattered along the lower third; keep the upper half calm and almost plain for a product title."),
    ("berry", "Raspberry pink to soft cream gradient with a cheerful pattern of berries, oats and small sparkles along the sides and bottom; keep the centre calm for a product title."),
    ("duck", "Warm honey-orange and cream colour blocking with one large friendly minimalist duck character silhouette near the bottom and soft rounded shapes; keep the top calm for a title."),
    ("nut", "Warm kraft-paper texture with hand-drawn roasted chestnuts, almonds and small leaves in brown, gold and green along the bottom edge; keep the top two thirds calm for a title."),
    ("ocean", "Cool sky-blue to seafoam gradient with gentle wave lines, small fish and salt-crystal sparkles at the bottom; keep the upper half calm for a title."),
]


def load_key(path):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"sk-[A-Za-z0-9_\-]{20,}", text)
    if not match:
        raise SystemExit("No OpenAI key found in the key file")
    return match.group(0)


def generate(client, key, prompt, size):
    payload = {"model": MODEL, "prompt": prompt, "quality": QUALITY, "size": size, "n": 1, "output_format": "png"}
    response = client.post("https://api.openai.com/v1/images/generations", headers={"Authorization": "Bearer " + key}, json=payload)
    if response.status_code >= 400:
        raise SystemExit(f"Provider error {response.status_code}: {response.text[:300]}")
    body = response.json()
    raw = base64.b64decode(body["data"][0]["b64_json"])
    return raw, body.get("usage", {}), response.headers.get("x-request-id")


def estimate_cost(usage):
    # Published GPT Image 2.5 token prices: text input $5/M, image input $8/M, image output $30/M (estimate only).
    text_in = usage.get("input_tokens_details", {}).get("text_tokens", usage.get("input_tokens", 0))
    image_in = usage.get("input_tokens_details", {}).get("image_tokens", 0)
    out = usage.get("output_tokens", 0)
    return round(text_in * 5 / 1e6 + image_in * 8 / 1e6 + out * 30 / 1e6, 4)


def save(raw, out, fmt):
    with Image.open(BytesIO(raw)) as image:
        image = image.convert("RGB")
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "webp":
            image.save(out, format="WEBP", quality=92, method=6)
        else:
            image.save(out, format="PNG", optimize=True)
        return image.size


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--set", choices=["site", "backgrounds"], required=True)
    parser.add_argument("--only", help="comma separated output names to (re)generate")
    args = parser.parse_args()
    key = load_key(args.key_file)
    only = set(args.only.split(",")) if args.only else None
    records = []
    total = 0.0
    with httpx.Client(timeout=httpx.Timeout(300, connect=15)) as client:
        if args.set == "site":
            jobs = [(Path(item["out"]).name, ROOT / item["out"], item["size"], item["prompt"], "webp") for item in SITE]
        else:
            jobs = [(f"{name}.webp", ROOT / "fixtures" / "demo-backgrounds" / f"{name}.webp", "1024x1536",
                     "Use case: print artwork. Flat, print-ready background artwork for a Korean food pouch, portrait, full bleed, not a photograph of a pouch, no mockup, no perspective. "
                     + STYLE + NO_TEXT + detail + " Smooth print-safe colours, no harsh vignette.", "webp") for name, detail in BACKGROUNDS]
        for name, out, size, prompt, fmt in jobs:
            if only and name not in only:
                continue
            print("generating", name, size, flush=True)
            raw, usage, request_id = generate(client, key, prompt, size)
            width, height = save(raw, out, fmt)
            cost = estimate_cost(usage)
            total += cost
            records.append({"file": str(out.relative_to(ROOT)).replace("\\", "/"), "width": width, "height": height, "bytes": out.stat().st_size,
                            "sha256": sha256(out.read_bytes()).hexdigest(), "model": MODEL, "quality": QUALITY, "size": size, "prompt": prompt,
                            "usage": usage, "estimated_cost_usd": cost, "request_id_recorded": bool(request_id)})
            print(f"  saved {out.name} {width}x{height} ~${cost}", flush=True)
    log = ROOT / "docs" / ("media-images.json" if args.set == "site" else "demo-backgrounds.json")
    existing = json.loads(log.read_text(encoding="utf-8")) if log.exists() else {}
    # Merge by file so partial (--only) runs keep earlier records and accumulate the estimated cost.
    kept = [item for item in existing.get("images", []) if item.get("model") == MODEL and item["file"] not in {r["file"] for r in records}]
    payload = {"created_at": datetime.now(timezone.utc).isoformat(), "method": "scripts/generate-brand-media.py", "model": MODEL, "quality": QUALITY,
               "estimated_total_cost_usd": round(total + float(existing.get("estimated_total_cost_usd", 0) if kept else 0), 4), "images": kept + records}
    if args.set == "site" and existing.get("model") != MODEL:
        payload["previous"] = existing or None
    log.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"images": len(records), "estimated_total_cost_usd": round(total, 4), "log": str(log.relative_to(ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
