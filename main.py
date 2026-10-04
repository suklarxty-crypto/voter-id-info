# main.py - ECI Voter Info API (Bulletproof View Details)
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
# AUTH
# ==============================================

def require_api_key(f):
    @wraps(f)
    def deco(*a, **k):
        key = k.get('key', '').strip()
        if not key:
            return jsonify({"status": "error", "error_code": "MISSING_API_KEY",
                            "message": "API key required"}), 401
        if key not in VALID_KEYS:
            return jsonify({"status": "error", "error_code": "INVALID_API_KEY",
                            "message": "Invalid API key"}), 403
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
            await page.wait_for_timeout(200)
            return
    except:
        pass
    await page.wait_for_timeout(200)


# ==============================================
# 🎯 BULLETPROOF VIEW DETAILS CLICKER
# ==============================================

async def find_and_click_view_details(page, ctx):
    """
    Try 12 different methods to find & click View Details
    Returns: (success: bool, method: str, new_page or None)
    """
    
    methods_tried = []
    
    # ═══════════════════════════════════════
    # METHOD 1: JavaScript click by exact text
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('button, a, span, div, td')) {
                const t = (el.innerText || '').trim();
                if (t === 'View Details') {
                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    el.click();
                    return {success: true, tag: el.tagName, text: t};
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, f"m1_js_exact_{result.get('tag')}", None
    except Exception as e:
        methods_tried.append(f"m1_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 2: href with viewdetail
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('a[href]')) {
                const h = (el.href || '').toLowerCase();
                if (h.includes('viewdetail') || h.includes('view-detail')) {
                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    el.click();
                    return {success: true, href: el.href};
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m2_href_viewdetail", None
    except Exception as e:
        methods_tried.append(f"m2_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 3: onclick attribute
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('[onclick]')) {
                const t = (el.innerText || '').trim();
                if (t.toLowerCase().includes('view') || t.toLowerCase().includes('detail')) {
                    el.click();
                    return {success: true, text: t};
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m3_onclick", None
    except Exception as e:
        methods_tried.append(f"m3_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 4: Case-insensitive exact match
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('button, a')) {
                const t = (el.innerText || '').trim().toLowerCase();
                if (t === 'view details' || t === 'view detail' || t === 'details') {
                    el.scrollIntoView({block: 'center', behavior: 'instant'});
                    el.click();
                    return {success: true, text: el.innerText.trim()};
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m4_lowercase_exact", None
    except Exception as e:
        methods_tried.append(f"m4_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 5: Playwright get_by_text (exact)
    # ═══════════════════════════════════════
    try:
        loc = page.get_by_text("View Details", exact=True)
        count = await loc.count()
        if count > 0:
            await loc.first.scroll_into_view_if_needed(timeout=2000)
            await loc.first.click(timeout=2000)
            return True, "m5_get_by_text_exact", None
    except Exception as e:
        methods_tried.append(f"m5_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 6: Playwright get_by_role button
    # ═══════════════════════════════════════
    try:
        loc = page.get_by_role("button", name=re.compile(r"view\s*detail", re.IGNORECASE))
        count = await loc.count()
        if count > 0:
            await loc.first.scroll_into_view_if_needed(timeout=2000)
            await loc.first.click(timeout=2000)
            return True, "m6_get_by_role_button", None
    except Exception as e:
        methods_tried.append(f"m6_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 7: Playwright get_by_role link
    # ═══════════════════════════════════════
    try:
        loc = page.get_by_role("link", name=re.compile(r"view\s*detail", re.IGNORECASE))
        count = await loc.count()
        if count > 0:
            await loc.first.scroll_into_view_if_needed(timeout=2000)
            await loc.first.click(timeout=2000)
            return True, "m7_get_by_role_link", None
    except Exception as e:
        methods_tried.append(f"m7_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 8: Force click via JS (bypass event handlers)
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || '').trim();
                if (t === 'View Details' || t === 'view details') {
                    // Dispatch full click sequence
                    ['mousedown', 'mouseup', 'click'].forEach(evt => {
                        el.dispatchEvent(new MouseEvent(evt, {bubbles: true, cancelable: true}));
                    });
                    return {success: true, tag: el.tagName};
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m8_force_click", None
    except Exception as e:
        methods_tried.append(f"m8_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 9: Look for "Details" keyword anywhere
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('button, a')) {
                const t = (el.innerText || '').trim();
                if (t.length > 0 && t.length < 30 && /detail/i.test(t)) {
                    el.scrollIntoView({block: 'center'});
                    el.click();
                    return {success: true, text: t};
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m9_detail_keyword", None
    except Exception as e:
        methods_tried.append(f"m9_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 10: Table row click (some ECI versions)
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            // Look for table with data and click last cell or action cell
            const tables = document.querySelectorAll('table');
            for (const tbl of tables) {
                const rows = tbl.querySelectorAll('tbody tr, tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        // Click last cell
                        const lastCell = cells[cells.length - 1];
                        const t = (lastCell.innerText || '').trim();
                        if (t && t.length < 30 && /view|detail|show|more/i.test(t)) {
                            lastCell.click();
                            return {success: true, text: t};
                        }
                    }
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m10_table_row", None
    except Exception as e:
        methods_tried.append(f"m10_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 11: Look for result card and click
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            const cardSelectors = [
                '[class*="voter"]', '[class*="result"]', '[class*="detail"]',
                '[class*="card"]', '[class*="search-result"]'
            ];
            for (const sel of cardSelectors) {
                for (const card of document.querySelectorAll(sel)) {
                    const t = (card.innerText || '').toLowerCase();
                    if (t.includes('view') && t.includes('detail')) {
                        // Click a link inside
                        const link = card.querySelector('a, button');
                        if (link) {
                            link.click();
                            return {success: true, card_selector: sel};
                        }
                        card.click();
                        return {success: true, card_selector: sel, direct: true};
                    }
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m11_result_card", None
    except Exception as e:
        methods_tried.append(f"m11_fail:{str(e)[:50]}")
    
    # ═══════════════════════════════════════
    # METHOD 12: Find by aria-label or title
    # ═══════════════════════════════════════
    try:
        result = await page.evaluate("""() => {
            const attrs = ['aria-label', 'title', 'data-action', 'data-testid'];
            for (const attr of attrs) {
                for (const el of document.querySelectorAll(`[${attr}]`)) {
                    const v = (el.getAttribute(attr) || '').toLowerCase();
                    if (v.includes('view') && v.includes('detail')) {
                        el.click();
                        return {success: true, attr: attr, value: v};
                    }
                }
            }
            return {success: false};
        }""")
        if result.get("success"):
            return True, "m12_attr_based", None
    except Exception as e:
        methods_tried.append(f"m12_fail:{str(e)[:50]}")
    
    return False, "all_failed", None


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
                "--single-process",
                "--disable-software-rasterizer",
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

            # Load page
            try:
                await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                await browser.close()
                return {"error": "PAGE_LOAD_FAILED",
                        "message": f"Load failed: {str(e)[:150]}"}

            await page.wait_for_timeout(1200)

            # EPIC tab
            for sel in ['text=Search by EPIC', 'text=ईपीआईसी द्वारा खोजें',
                        'button:has-text("EPIC")']:
                try:
                    await page.click(sel, timeout=800)
                    break
                except:
                    continue

            await page.wait_for_timeout(300)

            # Fill EPIC
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

            # API response hook
            api_resp = {"data": None}

            async def on_resp(resp):
                try:
                    u = resp.url.lower()
                    if "search-by-epic" in u or "searchbyepic" in u:
                        body_text = await resp.text()
                        api_resp["data"] = {
                            "status": resp.status,
                            "body": body_text[:2000]
                        }
                except:
                    pass

            page.on("response", on_resp)

            # Captcha loop
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

                # Fill captcha
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

                # Click SEARCH
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

                # Wait for response
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

            # ═══════════════════════════════════════════════
            # ⚡ CHECK API RESPONSE FOR ERRORS FIRST
            # ═══════════════════════════════════════════════
            if api_resp["data"]:
                body_str = api_resp["data"]["body"].lower()
                error_keywords = [
                    "no data found", "not found", "no record", "no voter",
                    "invalid epic", "not registered", "no results"
                ]
                for err in error_keywords:
                    if err in body_str:
                        await browser.close()
                        return {"error": "NO_DATA",
                                "message": f"No voter record found for EPIC: {epic}"}

            # ═══════════════════════════════════════════════
            # ⚡ WAIT + RETRY FOR VIEW DETAILS (MULTIPLE ROUNDS)
            # ═══════════════════════════════════════════════
            
            vd_success = False
            vd_method = ""
            new_page = None
            
            # Round 1: Fast waits (10 × 400ms = 4s)
            for wait_i in range(10):
                await page.wait_for_timeout(400)
                
                success, method, np = await find_and_click_view_details(page, ctx)
                if success:
                    vd_success = True
                    vd_method = f"round1_{method}"
                    new_page = np
                    break
                
                # Check if URL changed (some versions redirect)
                try:
                    if "viewdetail" in page.url.lower():
                        vd_success = True
                        vd_method = "round1_url_change"
                        break
                except:
                    pass
            
            # Round 2: Extended waits (10 × 800ms = 8s)
            if not vd_success:
                for wait_i in range(10):
                    await page.wait_for_timeout(800)
                    
                    success, method, np = await find_and_click_view_details(page, ctx)
                    if success:
                        vd_success = True
                        vd_method = f"round2_{method}"
                        new_page = np
                        break
                    
                    try:
                        if "viewdetail" in page.url.lower():
                            vd_success = True
                            vd_method = "round2_url_change"
                            break
                    except:
                        pass
                    
                    # Scroll to trigger lazy load
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except:
                        pass
            
            # Round 3: Very long wait (5 × 2s = 10s)
            if not vd_success:
                for wait_i in range(5):
                    await page.wait_for_timeout(2000)
                    
                    success, method, np = await find_and_click_view_details(page, ctx)
                    if success:
                        vd_success = True
                        vd_method = f"round3_{method}"
                        new_page = np
                        break
                    
                    try:
                        if "viewdetail" in page.url.lower():
                            vd_success = True
                            vd_method = "round3_url_change"
                            break
                    except:
                        pass
            
            if not vd_success:
                # Get body for diagnosis
                try:
                    body = await page.evaluate("() => document.body.innerText")
                except:
                    body = ""
                
                await browser.close()
                return {
                    "error": "VIEW_DETAILS_FAILED",
                    "message": "View Details button not found after all 12 methods",
                    "body_preview": body[:500] if body else ""
                }

            # ═══════════════════════════════════════════════
            # WAIT FOR DETAIL PAGE
            # ═══════════════════════════════════════════════
            detail_ready = False
            
            # Check if we already have new_page reference
            if new_page:
                page = new_page
                detail_ready = True
            else:
                # Wait for URL change
                for _ in range(50):
                    try:
                        if "viewdetail" in page.url.lower():
                            detail_ready = True
                            break
                    except:
                        pass
                    await page.wait_for_timeout(150)
                
                # Check new tabs
                if not detail_ready:
                    for pg in ctx.pages:
                        if "viewdetail" in pg.url.lower():
                            page = pg
                            detail_ready = True
                            break

            if not detail_ready:
                await browser.close()
                return {"error": "DETAIL_PAGE_FAILED",
                        "message": "Detail page not loaded"}

            # ═══════════════════════════════════════════════
            # WAIT FOR SPA RENDER + EXTRACT
            # ═══════════════════════════════════════════════
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except:
                pass
            await page.wait_for_timeout(2000)

            body = await page.evaluate("() => document.body.innerText")
            if not body or len(body) < 100:
                await page.wait_for_timeout(2000)
                body = await page.evaluate("() => document.body.innerText")

            d = extract_detail(body)
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
        "version": "1.0.0",
        "endpoints": {
            "/api/voterid": {
                "example": "/api/voterid?key={your_api_key}&epic_number={epic}"
            }
        },
        "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
    })


@app.route('/api/voterid', methods=['GET'])
def voterid_query():
    k = request.args.get('key', '').strip()
    e = request.args.get('epic_number', '').strip()
    
    if not k:
        return jsonify({"status": "error", "error_code": "MISSING_API_KEY",
                        "message": "API key required"}), 401
    
    if k not in VALID_KEYS:
        return jsonify({"status": "error", "error_code": "INVALID_API_KEY",
                        "message": "Invalid API key"}), 403
    
    if not e:
        return jsonify({"status": "error", "error_code": "MISSING_EPIC",
                        "message": "EPIC number required"}), 400
    
    return process_voter(e)


@app.route('/api/voterid/key=<key>/epic_number=<epic>', methods=['GET'])
@require_api_key
def voterid_path(key, epic):
    return process_voter(epic)


@app.route('/cache/stats', methods=['GET'])
def cache_stats_ep():
    return jsonify(cache_stats())


@app.route('/cache/clear', methods=['GET'])
def cache_clear_ep():
    epic = request.args.get('epic', '').strip().upper()
    ok = cache_clear(epic) if epic else cache_clear()
    return jsonify({"success": ok})


def process_voter(epic):
    ok, result = validate_epic(epic)
    if not ok:
        return jsonify({"status": "error", "error_code": "INVALID_EPIC",
                        "message": result}), 400
    
    epic_clean = result
    start = time.time()
    
    # Cache
    cached = cache_get(epic_clean)
    if cached:
        cached.pop("_ts", None)
        cached["response_time"] = f"{round((time.time()-start)*1000,2)}ms"
        cached["_from_cache"] = True
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
                "response_time": f"{total}ms"
            }), 400
        
        out = {
            "status": "success",
            "epic": epic_clean,
            "detail": result.get("detail", {}),
            "response_time": f"{total}ms",
            "_from_cache": False
        }
        
        cache_set(epic_clean, out)
        return jsonify(out), 200
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "error_code": "FATAL_ERROR",
            "message": str(e)[:200]
        }), 500


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "ocr_loaded": get_ocr() is not None,
        "playwright_loaded": get_pw() is not None,
        "cache_entries": cache_stats().get("total_entries", 0)
    })


@app.errorhandler(404)
def nf(e):
    return jsonify({"status": "error", "error_code": "NOT_FOUND",
                    "message": "Endpoint not found"}), 404


@app.errorhandler(405)
def mna(e):
    return jsonify({"status": "error", "error_code": "METHOD_NOT_ALLOWED",
                    "message": "Only GET allowed"}), 405


@app.errorhandler(500)
def ie(e):
    return jsonify({"status": "error", "error_code": "INTERNAL_ERROR",
                    "message": "Internal error"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("=" * 60)
    print("🗳️ ECI VOTER INFO API v2.0 (BULLETPROOF)")
    print("=" * 60)
    print(f"🚀 Port: {port}")
    print("🔑 Key: QWM")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
