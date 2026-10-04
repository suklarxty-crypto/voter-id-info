# main.py - ECI Voter Info API (Render Docker)
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
from functools import wraps
from collections import Counter

# ==============================================
# 🗳️ ECI VOTER INFO API
# Made by @KINGFFAIAK47x · ANSH AFT
# ==============================================

app = Flask(__name__)

# ==============================================
# API KEYS (HIDDEN)
# ==============================================

VALID_KEYS = {
    "QWM": "full_access"
}

# ==============================================
# CONSTANTS
# ==============================================

BASE = "https://electoralsearch.eci.gov.in"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/154.0.0.0 Safari/537.36")

CAPTCHA_LEN = 6

# ==============================================
# LAZY LOADERS
# ==============================================

_ocr = None
_playwright_mod = None


def get_ocr():
    global _ocr
    if _ocr is None:
        try:
            import ddddocr
            _ocr = ddddocr.DdddOcr(show_ad=False)
            print("✅ ddddocr loaded")
        except Exception as e:
            print(f"❌ ddddocr load failed: {e}")
            return None
    return _ocr


def get_playwright():
    global _playwright_mod
    if _playwright_mod is None:
        try:
            from playwright.async_api import async_playwright
            _playwright_mod = async_playwright
            print("✅ Playwright loaded")
        except Exception as e:
            print(f"❌ Playwright load failed: {e}")
            return None
    return _playwright_mod


# ==============================================
# AUTHENTICATION
# ==============================================

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = kwargs.get('key', '').strip()
        
        if not api_key:
            return jsonify({
                "status": "error",
                "error_code": "MISSING_API_KEY",
                "message": "API key required",
                "usage": "/api/voterid?key={your_api_key}&epic_number={epic}",
                "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
            }), 401
        
        if api_key not in VALID_KEYS:
            return jsonify({
                "status": "error",
                "error_code": "INVALID_API_KEY",
                "message": "Invalid API key",
                "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
            }), 403
        
        return f(*args, **kwargs)
    return decorated_function


# ==============================================
# VALIDATION
# ==============================================

def validate_epic(epic):
    if not epic:
        return False, "EPIC number required"
    
    epic = str(epic).strip().upper()
    epic = re.sub(r'\s+', '', epic)
    
    if len(epic) < 3:
        return False, "EPIC number too short (min 3 chars)"
    
    if len(epic) > 20:
        return False, "EPIC number too long (max 20 chars)"
    
    if not re.match(r'^[A-Z0-9]+$', epic):
        return False, "EPIC must contain only letters and numbers"
    
    return True, epic


# ==============================================
# CAPTCHA OCR
# ==============================================

def preprocess(img_bytes, mode=0):
    try:
        from PIL import Image, ImageOps, ImageFilter
        img = Image.open(io.BytesIO(img_bytes))
    except Exception:
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


def clean_ocr(txt):
    if not txt:
        return ""
    return "".join(c for c in txt if c.isalnum())


def ocr_multi(img_bytes):
    ocr = get_ocr()
    if not ocr or not img_bytes:
        return ""
    
    results = []
    
    try:
        r0 = clean_ocr(ocr.classification(img_bytes))
        if r0:
            results.append(r0)
    except Exception:
        pass
    
    for mode in (1, 2, 3, 4, 5):
        try:
            pimg = preprocess(img_bytes, mode)
            if pimg is None:
                continue
            buf = io.BytesIO()
            pimg.save(buf, format="PNG")
            r = clean_ocr(ocr.classification(buf.getvalue()))
            if r:
                results.append(r)
        except Exception:
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
            await page.wait_for_timeout(300)
            return
    except Exception:
        pass
    await page.wait_for_timeout(300)


# ==============================================
# MAIN SEARCH FLOW
# ==============================================

async def run_search(epic):
    async_playwright = get_playwright()
    if not async_playwright:
        return {
            "status": "error",
            "error_code": "PLAYWRIGHT_MISSING",
            "message": "Playwright not installed. Install: pip install playwright && playwright install chromium"
        }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
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
                return {
                    "status": "error",
                    "error_code": "PAGE_LOAD_FAILED",
                    "message": f"Failed to load ECI page: {str(e)[:150]}"
                }

            await page.wait_for_timeout(700)

            # Click EPIC tab
            for sel in ['text=Search by EPIC', 'text=ईपीआईसी द्वारा खोजें',
                        'button:has-text("EPIC")']:
                try:
                    await page.click(sel, timeout=800)
                    break
                except Exception:
                    continue

            await page.wait_for_timeout(250)

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
                return {
                    "status": "error",
                    "error_code": "EPIC_FILL_FAILED",
                    "message": "Could not fill EPIC field"
                }

            await page.wait_for_timeout(200)

            # Search response hook
            search_ok = {"v": False}

            async def on_resp(resp):
                try:
                    u = resp.url.lower()
                    if resp.status == 200 and ("search-by-epic" in u or "searchbyepic" in u):
                        search_ok["v"] = True
                except Exception:
                    pass

            page.on("response", on_resp)

            # Captcha + search loop
            last_txt = None
            search_done = False
            captcha_attempts = 0
            max_attempts = 15

            for attempt in range(1, max_attempts + 1):
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
                    await page.wait_for_timeout(300)
                    continue

                img_b64 = cap_src.split(",", 1)[1] if "," in cap_src else cap_src
                try:
                    img_bytes = base64.b64decode(img_b64)
                except Exception:
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

                search_ok["v"] = False
                await page.mouse.click(clicked["x"], clicked["y"])

                got = False
                for _ in range(50):
                    if search_ok["v"]:
                        got = True
                        break
                    try:
                        err = await page.evaluate(
                            "() => /invalid captcha|enter valid captcha/i.test(document.body.innerText)")
                        if err:
                            break
                    except Exception:
                        pass
                    await page.wait_for_timeout(100)

                if got:
                    search_done = True
                    break

                await click_refresh(page)

            if not search_done:
                await browser.close()
                return {
                    "status": "error",
                    "error_code": "SEARCH_FAILED",
                    "message": f"Search failed after {captcha_attempts} attempts. Captcha OCR may have failed.",
                    "attempts": captcha_attempts
                }

            # Click View Details
            await page.wait_for_timeout(500)

            clicked_vd = await page.evaluate("""() => {
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

            if not clicked_vd:
                await browser.close()
                return {
                    "status": "error",
                    "error_code": "VIEW_DETAILS_FAILED",
                    "message": "View Details button not found"
                }

            # Wait for detail page
            detail_ready = False
            for _ in range(40):
                try:
                    cur = page.url
                    if "viewdetail" in cur.lower():
                        detail_ready = True
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(200)

            if not detail_ready:
                for pg in ctx.pages:
                    if "viewdetail" in pg.url.lower():
                        page = pg
                        detail_ready = True
                        break

            if not detail_ready:
                await browser.close()
                return {
                    "status": "error",
                    "error_code": "DETAIL_PAGE_FAILED",
                    "message": "Detail page not detected"
                }

            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)

            body = await page.evaluate("() => document.body.innerText")
            if not body or len(body) < 100:
                await page.wait_for_timeout(2000)
                body = await page.evaluate("() => document.body.innerText")

            d = extract_detail(body)

            await browser.close()

            return {
                "status": "success",
                "epic": epic,
                "detail": d,
                "raw_length": len(body) if body else 0,
                "credit": {
                    "username": "@KINGFFAIAK47x",
                    "made_by": "ANSH AFT"
                }
            }

        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return {
                "status": "error",
                "error_code": "UNKNOWN_ERROR",
                "message": str(e)[:200],
                "traceback": traceback.format_exc()[:500]
            }


# ==============================================
# ENDPOINTS
# ==============================================

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "service": "🗳️ ECI Voter Info API",
        "version": "1.0.0",
        "description": "Get Indian voter details from ECI",
        "endpoints": {
            "/api/voterid": {
                "method": "GET",
                "description": "Get voter details by EPIC number",
                "example": "/api/voterid?key={your_api_key}&epic_number={epic_number}"
            },
            "/health": {
                "method": "GET",
                "description": "Health check"
            }
        },
        "credit": {
            "username": "@KINGFFAIAK47x",
            "made_by": "ANSH AFT"
        }
    })


@app.route('/api/voterid', methods=['GET'])
def voterid_query():
    api_key = request.args.get('key', '').strip()
    epic = request.args.get('epic_number', '').strip()
    
    if not api_key:
        return jsonify({
            "status": "error",
            "error_code": "MISSING_API_KEY",
            "message": "API key required",
            "usage": "/api/voterid?key={your_api_key}&epic_number={epic}",
            "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
        }), 401
    
    if api_key not in VALID_KEYS:
        return jsonify({
            "status": "error",
            "error_code": "INVALID_API_KEY",
            "message": "Invalid API key",
            "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
        }), 403
    
    if not epic:
        return jsonify({
            "status": "error",
            "error_code": "MISSING_EPIC",
            "message": "EPIC number required",
            "usage": "/api/voterid?key={your_api_key}&epic_number={epic}",
            "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
        }), 400
    
    return process_voter(epic)


@app.route('/api/voterid/key=<key>/epic_number=<epic>', methods=['GET'])
@require_api_key
def voterid_path(key, epic):
    return process_voter(epic)


def process_voter(epic):
    is_valid, result = validate_epic(epic)
    if not is_valid:
        return jsonify({
            "status": "error",
            "error_code": "INVALID_EPIC",
            "message": result,
            "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
        }), 400
    
    epic_clean = result
    start_time = time.time()
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(run_search(epic_clean))
        finally:
            loop.close()
        
        total_time = round((time.time() - start_time) * 1000, 2)
        result["response_time"] = f"{total_time}ms"
        
        if result.get("status") == "success":
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        return jsonify({
            "status": "error",
            "error_code": "FATAL_ERROR",
            "message": str(e)[:200],
            "traceback": traceback.format_exc()[:500],
            "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
        }), 500


@app.route('/health', methods=['GET'])
def health():
    ocr_ok = get_ocr() is not None
    pw_ok = get_playwright() is not None
    
    return jsonify({
        "status": "healthy",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ocr_loaded": ocr_ok,
        "playwright_loaded": pw_ok,
        "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
    })


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "status": "error",
        "error_code": "NOT_FOUND",
        "message": "Endpoint not found",
        "usage": "/api/voterid?key=your_api_key&epic_number=ABC1234567",
        "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
    }), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        "status": "error",
        "error_code": "METHOD_NOT_ALLOWED",
        "message": "Only GET requests allowed",
        "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
    }), 405


@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "status": "error",
        "error_code": "INTERNAL_ERROR",
        "message": "Internal server error",
        "credit": {"username": "@KINGFFAIAK47x", "made_by": "ANSH AFT"}
    }), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("=" * 60)
    print("🗳️ ECI VOTER INFO API v1.0.0")
    print("=" * 60)
    print(f"🚀 Running on: http://localhost:{port}")
    print("\n🔑 Key: QWM")
    print("\n📌 Endpoints:")
    print("  /api/voterid?key=your_api_key&epic_number=ABC1234567")
    print("  /api/voterid/key=your_api_key/epic_number=ABC1234567")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
