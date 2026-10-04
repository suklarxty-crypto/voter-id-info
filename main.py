# main.py - ECI Voter Info API (FINAL COMPLETE) — FULL FIELD NAMES + CREDIT
# Made by @KINGFFAIAK47x · ANSH AFT

from flask import Flask, jsonify, request
import asyncio
import base64
import io
import json
import os
import re
import time
import traceback
import hashlib
from functools import wraps
from collections import Counter
from datetime import datetime

app = Flask(__name__)

# ⚡ Suppress ONNX warnings
os.environ["ONNX_RUNTIME_LOG_LEVEL"] = "3"
os.environ["ORT_LOGGING_LEVEL"] = "3"

import logging
logging.getLogger("onnxruntime").setLevel(logging.ERROR)

# ==============================================
# CONFIG
# ==============================================

VALID_KEYS = {"QWM": "full_access"}

BASE = "https://electoralsearch.eci.gov.in"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/154.0.0.0 Safari/537.36")

CAPTCHA_LEN = 6

CACHE_DIR = "/tmp/eci_cache"
CACHE_TTL = 86400
os.makedirs(CACHE_DIR, exist_ok=True)

# ⚡ CREDIT INFO
CREDIT = {
    "username": "@KINGFFAIAK47x",
    "made_by": "ANSH AFT"
}

_ocr = None
_pw_mod = None


def get_ocr():
    global _ocr
    if _ocr is None:
        try:
            import ddddocr
            _ocr = ddddocr.DdddOcr(show_ad=False)
        except:
            return None
    return _ocr


def get_pw():
    global _pw_mod
    if _pw_mod is None:
        try:
            from playwright.async_api import async_playwright
            _pw_mod = async_playwright
        except:
            return None
    return _pw_mod


# ==============================================
# CACHE
# ==============================================

def _cp(epic):
    return os.path.join(CACHE_DIR, f"{hashlib.md5(epic.encode()).hexdigest()}.json")


def cache_get(epic):
    p = _cp(epic)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - d.get("_ts", 0) > CACHE_TTL:
            try:
                os.remove(p)
            except:
                pass
            return None
        return d
    except:
        return None


def cache_set(epic, data):
    try:
        c = data.copy()
        c["_ts"] = time.time()
        with open(_cp(epic), "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
        return True
    except:
        return False


def cache_clear(epic=None):
    if epic:
        p = _cp(epic)
        if os.path.exists(p):
            try:
                os.remove(p)
                return True
            except:
                pass
        return False
    try:
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json"):
                os.remove(os.path.join(CACHE_DIR, f))
        return True
    except:
        return False


def cache_stats():
    try:
        fs = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
        sz = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in fs)
        return {"total_entries": len(fs), "size_kb": round(sz / 1024, 2),
                "ttl_seconds": CACHE_TTL}
    except Exception as e:
        return {"error": str(e)}


# ==============================================
# AUTH + VALIDATION
# ==============================================

def require_api_key(f):
    @wraps(f)
    def deco(*a, **k):
        key = k.get('key', '').strip()
        if not key:
            return jsonify({"status": "error", "error_code": "MISSING_API_KEY",
                            "message": "API key required",
                            "credit": CREDIT}), 401
        if key not in VALID_KEYS:
            return jsonify({"status": "error", "error_code": "INVALID_API_KEY",
                            "message": "Invalid API key",
                            "credit": CREDIT}), 403
        return f(*a, **k)
    return deco


def validate_epic(e):
    if not e:
        return False, "EPIC required"
    e = re.sub(r'\s+', '', str(e).strip().upper())
    if len(e) < 3:
        return False, "EPIC too short"
    if len(e) > 20:
        return False, "EPIC too long"
    if not re.match(r'^[A-Z0-9]+$', e):
        return False, "EPIC must be alphanumeric"
    return True, e


# ==============================================
# CAPTCHA OCR
# ==============================================

def preprocess(img_bytes, mode=0):
    try:
        from PIL import Image, ImageOps, ImageFilter
        img = Image.open(io.BytesIO(img_bytes))
    except:
        return None
    if img.mode != "RGB":
        img = img.convert("RGB")
    if mode == 0:
        return img
    g = img.convert("L")
    if mode == 1:
        return ImageOps.autocontrast(g)
    if mode == 2:
        g = ImageOps.autocontrast(g)
        return g.point(lambda p: 255 if p > 150 else 0)
    if mode == 3:
        g = ImageOps.autocontrast(g)
        return g.point(lambda p: 255 if p > 180 else 0)
    if mode == 4:
        g = ImageOps.autocontrast(g)
        w, h = g.size
        g = g.resize((w * 3, h * 3), Image.LANCZOS)
        return g.filter(ImageFilter.MedianFilter(size=3))
    if mode == 5:
        g = ImageOps.autocontrast(g)
        g = g.point(lambda p: 255 if p > 140 else 0)
        return g.filter(ImageFilter.MaxFilter(size=3))
    return g


def ocr_multi(img_bytes):
    ocr = get_ocr()
    if not ocr or not img_bytes:
        return ""
    results = []
    try:
        r0 = "".join(c for c in (ocr.classification(img_bytes) or "") if c.isalnum())
        if r0:
            results.append(r0)
    except:
        pass
    for mode in (1, 2, 3, 4, 5):
        try:
            p = preprocess(img_bytes, mode)
            if p is None:
                continue
            buf = io.BytesIO()
            p.save(buf, format="PNG")
            r = "".join(c for c in (ocr.classification(buf.getvalue()) or "") if c.isalnum())
            if r:
                results.append(r)
        except:
            continue
    if not results:
        return ""
    valid = [r for r in results if len(r) == CAPTCHA_LEN]
    if valid:
        return Counter(valid).most_common(1)[0][0]
    results.sort(key=len, reverse=True)
    return results[0]


# ==============================================
# DETAIL EXTRACTION
# ==============================================

DETAIL_LABELS = [
    ("first_name", ["प्रथम नाम/First Name", "First Name"]),
    ("last_name", ["उपनाम/Last Name", "Last Name"]),
    ("rel_first", ["रिश्तेदार का प्रथम नाम/Relative's First Name", "Relative's First Name"]),
    ("rel_last", ["रिश्तेदार का उपनाम/Relative's Last Name", "Relative's Last Name"]),
    ("age", ["उम्र/Age", "Age"]),
    ("gender", ["लिंग/Gender", "Gender"]),
    ("epic", ["ईपीआईसी संख्या/EPIC No", "EPIC No"]),
    ("state", ["राज्य/State", "State"]),
    ("pc", ["संसदीय निर्वाचन क्षेत्र संख्या - संसदीय निर्वाचन क्षेत्र/Parliamentary Constituency Number-Parliamentary Constituency Name",
            "Parliamentary Constituency Number-Parliamentary Constituency Name"]),
    ("ac", ["विधान सभा निर्वाचन क्षेत्र संख्या - विधान सभा निर्वाचन क्षेत्र/Assembly Constituency Number-Assembly Constituency Name",
            "Assembly Constituency Number-Assembly Constituency Name"]),
    ("ps", ["मतदान केंद्र/Polling Station", "Polling Station"]),
    ("part", ["भाग संख्या-भाग का नाम/Part Number-Part Name", "Part Number-Part Name"]),
    ("psn", ["भाग मतदाता क्रमांक/Part Serial Number", "Part Serial Number"]),
    ("polling", ["मतदान की तारीख/Polling Date", "Polling Date"]),
]

STOP_MARKERS = [
    "Note 1", "Note :", "ऑनलाइन मतदाता पंजीकरण", "Online Voter Registration",
    "S. No. Designation", "चुनाव अधिकारियों", "Details of Election Officials",
    "This output is computer generated", "This is not an identity document",
    "नए मतदाता के", "प्रवासी मतदाता", "मौजूदा निर्वाचकों", "मतदाता सूची में आपत्ति",
    "सुधार/स्थानांतरण",
]


def extract_detail(body):
    if not body:
        return {}
    cut = len(body)
    for m in STOP_MARKERS:
        i = body.find(m)
        if i != -1 and i < cut:
            cut = i
    body = body[:cut]
    found = []
    for key, labels in DETAIL_LABELS:
        bp, bl = -1, 0
        for lbl in labels:
            i = body.find(lbl)
            if i != -1 and (bp == -1 or i < bp):
                bp, bl = i, len(lbl)
        if bp != -1:
            found.append((bp, key, bl))
    found.sort()
    out = {}
    for i, (pos, key, lblen) in enumerate(found):
        s = pos + lblen
        e = found[i+1][0] if i+1 < len(found) else len(body)
        v = " ".join(body[s:e].split()).strip(" :\t\n-|")
        out[key] = v
    return out


# ==============================================
# ⚡ FULL NAME COMBINATION
# ==============================================

def combine_names(detail):
    """Combine first+last → full_name, rel_first+rel_last → relative_full_name"""
    if not detail:
        return detail

    out = dict(detail)

    first = (out.get("first_name") or "").strip()
    last = (out.get("last_name") or "").strip()

    parts = []
    if first and first.upper() not in ("N/A", "NONE", "-"):
        parts.append(first)
    if last and last.upper() not in ("N/A", "NONE", "-"):
        parts.append(last)

    out["full_name"] = " ".join(parts) if parts else "N/A"

    rel_first = (out.get("rel_first") or "").strip()
    rel_last = (out.get("rel_last") or "").strip()

    rel_parts = []
    if rel_first and rel_first.upper() not in ("N/A", "NONE", "-"):
        rel_parts.append(rel_first)
    if rel_last and rel_last.upper() not in ("N/A", "NONE", "-"):
        rel_parts.append(rel_last)

    out["relative_full_name"] = " ".join(rel_parts) if rel_parts else "N/A"

    if parts and rel_parts:
        out["voter_with_relative"] = f"{out['full_name']} S/O {out['relative_full_name']}"
    else:
        out["voter_with_relative"] = out.get("full_name", "N/A")

    return out


# ==============================================
# ⚡ RENAME TO FULL FIELD NAMES
# ==============================================

def to_full_field_names(detail):
    """
    Convert short keys → full descriptive names.
    Example: ps → Polling Station, psn → Part Serial Number, ac → Assembly Constituency
    """
    if not detail:
        return {}

    FIELD_MAP = {
        "first_name":          "First Name",
        "last_name":           "Last Name",
        "full_name":           "Full Name",
        "rel_first":           "Relative's First Name",
        "rel_last":            "Relative's Last Name",
        "relative_full_name":  "Relative Full Name",
        "voter_with_relative": "Voter With Relative",
        "age":                 "Age",
        "gender":              "Gender",
        "epic":                "EPIC Number",
        "state":               "State",
        "pc":                  "Parliamentary Constituency",
        "ac":                  "Assembly Constituency",
        "ps":                  "Polling Station",
        "part":                "Part Number-Part Name",
        "psn":                 "Part Serial Number",
        "polling":             "Polling Date",
    }

    out = {}
    for k, v in detail.items():
        new_key = FIELD_MAP.get(k, k)
        out[new_key] = v
    return out


# ==============================================
# ⚡ PARSE API RESPONSE
# ==============================================

def parse_api_response(body_text):
    if not body_text:
        return False, 0, "Empty response"

    try:
        data = json.loads(body_text)
    except:
        body_lower = body_text.lower()
        if any(x in body_lower for x in ["invalid captcha", "enter valid captcha"]):
            return False, 0, "Invalid captcha"
        if any(x in body_lower for x in ["no data", "not found", "no record"]):
            return False, 0, "No data found"
        return True, 0, None

    if isinstance(data, dict):
        success = data.get("success", data.get("Success", True))
        if success is False:
            msg = data.get("message", data.get("Message", "Request failed"))
            return False, 0, msg

        records = data.get("data", data.get("result", data.get("records", [])))

        if isinstance(records, list):
            if len(records) == 0:
                return False, 0, "No voter records found"
            return True, len(records), None

        if isinstance(records, dict):
            inner = records.get("data", records.get("result", []))
            if isinstance(inner, list):
                if len(inner) == 0:
                    return False, 0, "No voter records found"
                return True, len(inner), None

        msg = data.get("message", data.get("Message", ""))
        if msg and any(x in str(msg).lower() for x in ["no", "not found", "invalid"]):
            return False, 0, str(msg)

        return True, 0, None

    if isinstance(data, list):
        if len(data) == 0:
            return False, 0, "No voter records found"
        return True, len(data), None

    return True, 0, None


# ==============================================
# BROWSER HELPERS
# ==============================================

async def block_heavy(route):
    u = route.request.url.lower()
    if any(x in u for x in [".woff", ".woff2", ".ttf", ".eot",
                             "google-analytics", "gtag", "hotjar",
                             "facebook", "doubleclick", "clarity.ms",
                             "googletagmanager"]):
        await route.abort()
    else:
        await route.continue_()


async def click_refresh(page):
    try:
        el = await page.query_selector('i.fa-rotate-right, i[class*="rotate"]')
        if el:
            await el.click()
            await page.wait_for_timeout(300)
            return
    except:
        pass
    await page.wait_for_timeout(300)


# ==============================================
# ⚡ FAST VD CLICKER
# ==============================================

async def find_and_click_vd(page):
    """Click View Details button"""

    try:
        ok = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('a, button')) {
                const t = (el.innerText || '').trim();
                if (t === 'View Details') {
                    el.scrollIntoView({block:'center'});
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
        if ok:
            return True, "exact_a_button"
    except:
        pass

    try:
        ok = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('span, div, td, a, button')) {
                const t = (el.innerText || '').trim();
                if (t === 'View Details') {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        el.scrollIntoView({block:'center'});
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        }""")
        if ok:
            return True, "exact_any"
    except:
        pass

    try:
        ok = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('a[href]')) {
                const h = (el.href || '').toLowerCase();
                if (h.includes('viewdetail') || h.includes('view-detail')) {
                    el.scrollIntoView({block:'center'});
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
        if ok:
            return True, "href"
    except:
        pass

    try:
        loc = page.get_by_text("View Details", exact=True)
        if await loc.count() > 0:
            await loc.first.scroll_into_view_if_needed(timeout=1500)
            await loc.first.click(timeout=1500)
            return True, "get_by_text"
    except:
        pass

    return False, "none"


# ==============================================
# MAIN SEARCH
# ==============================================

async def run_search(epic):
    pw = get_pw()
    if not pw:
        return {"error": "PLAYWRIGHT_MISSING", "message": "Playwright not installed"}

    async with pw() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        try:
            ctx = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=UA,
                locale="en-IN",
            )
            page = await ctx.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                window.chrome={runtime:{}};
            """)
            await page.route("**/*", block_heavy)

            try:
                await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                await browser.close()
                return {"error": "PAGE_LOAD_FAILED",
                        "message": f"Load failed: {str(e)[:150]}"}

            await page.wait_for_timeout(1000)

            for sel in ['text=Search by EPIC', 'text=ईपीआईसी द्वारा खोजें',
                        'button:has-text("EPIC")']:
                try:
                    await page.click(sel, timeout=800)
                    break
                except:
                    continue

            await page.wait_for_timeout(300)

            ok = await page.evaluate("""(epic) => {
                for (const e of document.querySelectorAll('input')) {
                    const s = (e.placeholder||'') + (e.name||'') + (e.id||'');
                    if (s.toLowerCase().includes('epic')) {
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        setter.call(e, epic);
                        e.dispatchEvent(new Event('input', {bubbles:true}));
                        e.dispatchEvent(new Event('change', {bubbles:true}));
                        return true;
                    }
                }
                return false;
            }""", epic)

            if not ok:
                await browser.close()
                return {"error": "EPIC_FILL_FAILED", "message": "EPIC field not found"}

            await page.wait_for_timeout(200)

            api_resp = {"data": None}

            async def on_resp(resp):
                try:
                    u = resp.url.lower()
                    if "search-by-epic" in u or "searchbyepic" in u:
                        body_text = await resp.text()
                        api_resp["data"] = {
                            "status": resp.status,
                            "body": body_text[:5000]
                        }
                except:
                    pass

            page.on("response", on_resp)

            last_txt = None
            search_done = False
            captcha_attempts = 0

            for attempt in range(1, 16):
                captcha_attempts = attempt

                cap_src = await page.evaluate("""() => {
                    for (const img of document.querySelectorAll('img')) {
                        const s = img.src || '';
                        if ((s.startsWith('data:image/jpg') || s.startsWith('data:image/jpeg')
                             || s.startsWith('data:image/png')) && s.length > 2000) {
                            const w = img.offsetWidth, h = img.offsetHeight;
                            if (w > 100 && h > 30 && w < 400 && h < 120) return s;
                        }
                    }
                    return null;
                }""")

                if not cap_src:
                    await page.wait_for_timeout(250)
                    continue

                img_b64 = cap_src.split(",", 1)[1] if "," in cap_src else cap_src
                try:
                    img_bytes = base64.b64decode(img_b64)
                except:
                    continue

                text = ocr_multi(img_bytes)

                if len(text) != CAPTCHA_LEN:
                    await click_refresh(page)
                    last_txt = None
                    continue

                if text == last_txt:
                    await click_refresh(page)
                    continue

                last_txt = text

                await page.evaluate("""(txt) => {
                    for (const e of document.querySelectorAll('input')) {
                        const s = (e.placeholder||'') + (e.name||'') + (e.id||'');
                        if (s.toLowerCase().includes('captcha')) {
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            setter.call(e, txt);
                            e.dispatchEvent(new Event('input', {bubbles:true}));
                            e.dispatchEvent(new Event('change', {bubbles:true}));
                            return true;
                        }
                    }
                    return false;
                }""", text)

                await page.wait_for_timeout(80)

                clicked = await page.evaluate("""() => {
                    for (const b of document.querySelectorAll('button')) {
                        const t = (b.innerText || '').trim();
                        if (t === 'SEARCH' && b.offsetWidth && !b.disabled) {
                            const r = b.getBoundingClientRect();
                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                    }
                    return null;
                }""")

                if not clicked:
                    continue

                api_resp["data"] = None
                await page.mouse.click(clicked["x"], clicked["y"])

                got = False
                for _ in range(80):
                    if api_resp["data"]:
                        got = True
                        break
                    try:
                        err = await page.evaluate(
                            "() => /invalid captcha|enter valid captcha/i.test(document.body.innerText)")
                        if err:
                            break
                    except:
                        pass
                    await page.wait_for_timeout(100)

                if got:
                    search_done = True
                    break

                await click_refresh(page)

            if not search_done:
                await browser.close()
                return {"error": "SEARCH_FAILED",
                        "message": f"Search failed after {captcha_attempts} attempts"}

            if api_resp["data"]:
                has_data, count, err_msg = parse_api_response(api_resp["data"]["body"])
                if not has_data:
                    await browser.close()
                    return {
                        "error": "NO_DATA",
                        "message": err_msg or f"No voter record found for EPIC: {epic}"
                    }

            await page.wait_for_timeout(500)

            vd_success = False
            vd_method = ""

            for _ in range(8):
                ok_vd, method = await find_and_click_vd(page)
                if ok_vd:
                    vd_success = True
                    vd_method = method
                    break
                try:
                    if "viewdetail" in page.url.lower():
                        vd_success = True
                        vd_method = "url_already"
                        break
                except:
                    pass
                await page.wait_for_timeout(400)

            if not vd_success:
                await browser.close()
                return {
                    "error": "VIEW_DETAILS_FAILED",
                    "message": "View Details button not found"
                }

            detail_ready = False

            for _ in range(60):
                try:
                    if "viewdetail" in page.url.lower() or "view-detail" in page.url.lower():
                        detail_ready = True
                        break
                except:
                    pass
                await page.wait_for_timeout(200)

            if not detail_ready:
                for _ in range(20):
                    for pg in ctx.pages:
                        try:
                            u = pg.url.lower()
                            if "viewdetail" in u or "view-detail" in u:
                                page = pg
                                detail_ready = True
                                break
                        except:
                            pass
                    if detail_ready:
                        break
                    await page.wait_for_timeout(300)

            if not detail_ready:
                await browser.close()
                return {
                    "error": "DETAIL_PAGE_FAILED",
                    "message": "Detail page not loaded (URL did not change to viewdetail)"
                }

            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass
            await page.wait_for_timeout(1500)

            body = ""
            for _ in range(5):
                try:
                    body = await page.evaluate("() => document.body.innerText")
                except:
                    body = ""

                if body and len(body) > 200:
                    if any(x in body for x in ["First Name", "प्रथम नाम", "EPIC No", "ईपीआईसी"]):
                        break

                await page.wait_for_timeout(1500)

            if not body or len(body) < 100:
                await browser.close()
                return {
                    "error": "DETAIL_EMPTY",
                    "message": "Detail page loaded but body was empty"
                }

            d = extract_detail(body)
            d = combine_names(d)
            d = to_full_field_names(d)

            await browser.close()

            return {
                "detail": d,
                "raw_length": len(body) if body else 0,
                "vd_method": vd_method
            }

        except Exception as e:
            try:
                await browser.close()
            except:
                pass
            return {"error": "UNKNOWN_ERROR", "message": str(e)[:200]}


# ==============================================
# ENDPOINTS
# ==============================================

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "🗳️ ECI Voter Info API",
        "version": "3.3.0",
        "endpoints": {
            "/api/voterid": {
                "example": "/api/voterid?key={your_api_key}&epic_number={epic}"
            }
        },
        "credit": CREDIT
    })


@app.route('/api/voterid', methods=['GET'])
def voterid_query():
    k = request.args.get('key', '').strip()
    e = request.args.get('epic_number', '').strip()

    if not k:
        return jsonify({"status": "error", "error_code": "MISSING_API_KEY",
                        "message": "API key required",
                        "credit": CREDIT}), 401

    if k not in VALID_KEYS:
        return jsonify({"status": "error", "error_code": "INVALID_API_KEY",
                        "message": "Invalid API key",
                        "credit": CREDIT}), 403

    if not e:
        return jsonify({"status": "error", "error_code": "MISSING_EPIC",
                        "message": "EPIC number required",
                        "credit": CREDIT}), 400

    return process_voter(e)


@app.route('/api/voterid/key=<key>/epic_number=<epic>', methods=['GET'])
@require_api_key
def voterid_path(key, epic):
    return process_voter(epic)


@app.route('/cache/stats', methods=['GET'])
def cache_stats_ep():
    return jsonify({
        "cache": cache_stats(),
        "credit": CREDIT
    })


@app.route('/cache/clear', methods=['GET'])
def cache_clear_ep():
    epic = request.args.get('epic', '').strip().upper()
    ok = cache_clear(epic) if epic else cache_clear()
    return jsonify({"success": ok, "credit": CREDIT})


def process_voter(epic):
    ok, result = validate_epic(epic)
    if not ok:
        return jsonify({"status": "error", "error_code": "INVALID_EPIC",
                        "message": result,
                        "credit": CREDIT}), 400

    epic_clean = result
    start = time.time()

    cached = cache_get(epic_clean)
    if cached:
        cached.pop("_ts", None)
        cached["response_time"] = f"{round((time.time()-start)*1000,2)}ms"
        cached["_from_cache"] = True
        cached["credit"] = CREDIT
        return jsonify(cached), 200

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(run_search(epic_clean))
        finally:
            loop.close()

        total = round((time.time()-start)*1000, 2)

        if "error" in result:
            return jsonify({
                "status": "error",
                "error_code": result["error"],
                "message": result.get("message", "Failed"),
                "response_time": f"{total}ms",
                "credit": CREDIT
            }), 400

        out = {
            "status": "success",
            "epic": epic_clean,
            "detail": result.get("detail", {}),
            "response_time": f"{total}ms",
            "_from_cache": False,
            "credit": CREDIT
        }

        cache_set(epic_clean, out)
        return jsonify(out), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "error_code": "FATAL_ERROR",
            "message": str(e)[:200],
            "credit": CREDIT
        }), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "ocr_loaded": get_ocr() is not None,
        "playwright_loaded": get_pw() is not None,
        "cache_entries": cache_stats().get("total_entries", 0),
        "credit": CREDIT
    })


@app.errorhandler(404)
def nf(e):
    return jsonify({"status": "error", "error_code": "NOT_FOUND",
                    "message": "Endpoint not found",
                    "credit": CREDIT}), 404


@app.errorhandler(405)
def mna(e):
    return jsonify({"status": "error", "error_code": "METHOD_NOT_ALLOWED",
                    "message": "Only GET allowed",
                    "credit": CREDIT}), 405


@app.errorhandler(500)
def ie(e):
    return jsonify({"status": "error", "error_code": "INTERNAL_ERROR",
                    "message": "Internal error",
                    "credit": CREDIT}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("=" * 60)
    print("🗳️ ECI VOTER INFO API v3.3 (FULL NAMES + CREDIT)")
    print("=" * 60)
    print(f"🚀 Port: {port}")
    print("🔑 Key: QWM")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
