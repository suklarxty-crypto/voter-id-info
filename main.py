# main.py - ECI Voter Info API (FINAL COMPLETE) — FIXED + CLEAN OUTPUT
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
# AUTH + VALIDATION
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
# ⚡ ECI RAW → CLEAN MAPPER
# ==============================================

def _get(content, *keys):
    """Case-insensitive field getter."""
    if not isinstance(content, dict):
        return ""
    for k in keys:
        for ck in (k, k.lower(), k.upper(), k.capitalize()):
            if ck in content:
                v = content[ck]
                if v is not None and str(v).strip() and str(v).strip().lower() != "null":
                    return str(v).strip()
    return ""


def _join_en_l1(en, l1):
    """Join English + regional language with space."""
    parts = []
    if en and en.strip():
        parts.append(en.strip())
    if l1 and l1.strip() and l1.strip() != en.strip():
        parts.append(l1.strip())
    return " ".join(parts).strip()


def map_eci_record(rec):
    """
    Convert raw ECI record (with content dict) → clean output format.

    Input example:
      {
        "content": {"Applicantfirstname": "Sujan", "Applicantfirstnamel1": "সুজন", ...},
        "id": "53621515_CQJ2509776_S25",
        "index": "national-electoral-display",
        "score": 16.45
      }

    Output:
      {
        "ac": "10-Kumargram", "age": "40", "epic": "CQJ2509776",
        "first_name": "Sujan সুজন", "full_name": "Sujan সুজন Debnath দেবনাথ",
        ...
      }
    """
    if not rec:
        return {}

    # unwrap content
    content = rec.get("content") or rec.get("Content") or {}
    if not isinstance(content, dict) or not content:
        # maybe the record itself is the content
        content = rec

    # ═══ Names ═══
    first_en = _get(content, "Applicantfirstname")
    first_l1 = _get(content, "Applicantfirstnamel1")
    first_name = _join_en_l1(first_en, first_l1)

    last_en = _get(content, "Applicantlastname")
    last_l1 = _get(content, "Applicantlastnamel1")
    last_name = _join_en_l1(last_en, last_l1)

    # fallback from Fullname
    if not first_name and not last_name:
        full_en = _get(content, "Fullname")
        full_l1 = _get(content, "Fullnamel1")
        full_name = _join_en_l1(full_en, full_l1)
        # split into first/last if possible
        parts = full_name.split()
        if parts:
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
    else:
        full_name = " ".join([x for x in [first_name, last_name] if x]).strip()

    # ═══ Relative names ═══
    rel_first_en = _get(content, "Relationname")
    rel_first_l1 = _get(content, "Relationnamel1")
    rel_first = _join_en_l1(rel_first_en, rel_first_l1)

    rel_last_en = _get(content, "Relationlname")
    rel_last_l1 = _get(content, "Relationlnamel1")
    rel_last = _join_en_l1(rel_last_en, rel_last_l1)

    rel_full_en = _get(content, "Relativefullname")
    rel_full_l1 = _get(content, "Relativefullnamel1")
    relative_full_name = _join_en_l1(rel_full_en, rel_full_l1)

    # fallback build relative full
    if not relative_full_name:
        relative_full_name = " ".join([x for x in [rel_first, rel_last] if x]).strip()

    # ═══ Relation prefix (S/O, D/O, W/O) ═══
    rel_type = _get(content, "Relationtype").upper()
    rel_prefix = {
        "FTHR": "S/O", "FATHER": "S/O", "F": "S/O",
        "MTHR": "D/O", "MOTHER": "D/O", "M": "D/O",
        "HUSB": "W/O", "HUSBAND": "W/O", "H": "W/O",
        "OTHER": "C/O",
    }.get(rel_type, "S/O")

    if full_name and relative_full_name:
        voter_with_relative = f"{full_name} {rel_prefix} {relative_full_name}"
    else:
        voter_with_relative = full_name or "N/A"

    # ═══ Gender ═══
    gen_raw = _get(content, "Gender").upper()
    gender = {
        "M": "Male", "MALE": "Male",
        "F": "Female", "FEMALE": "Female",
        "O": "Other", "T": "Transgender",
    }.get(gen_raw, gen_raw or "N/A")

    # ═══ PC ═══
    pc_no = _get(content, "Prlmntno")
    pc_name = _get(content, "Prlmntname")
    if pc_no and pc_name:
        pc = f"{pc_no}-{pc_name}"
    else:
        pc = pc_name or pc_no or "N/A"

    # ═══ AC ═══
    ac_no = _get(content, "Acnumber")
    ac_name = _get(content, "Asmblyname")
    if ac_no and ac_name:
        ac = f"{ac_no}-{ac_name}"
    else:
        ac = ac_name or ac_no or "N/A"

    # ═══ Part ═══
    part_no = _get(content, "Partnumber")
    part_name = _get(content, "Partname")
    if part_no and part_name:
        part = f"{part_no}-{part_name}"
    else:
        part = part_name or part_no or "N/A"

    # ═══ PS (Polling Station) — building + room + address ═══
    ps_building = _get(content, "Psbuildingname") or _get(content, "Partname")
    ps_room = _get(content, "Psroomdetails")
    ps_addr = _get(content, "Buildingaddress")
    ps_parts = [x for x in [ps_building, ps_room, ps_addr] if x]
    ps = " , ".join(ps_parts) if ps_parts else "N/A"

    # ═══ PSN ═══
    psn = _get(content, "Partserialnumber") or "N/A"

    # ═══ Polling date ═══
    polling = _get(content, "PollingDate", "Pollingdate") or "No elections scheduled currently"

    # ═══ Final clean dict ═══
    clean = {
        "ac": ac,
        "age": _get(content, "Age") or "N/A",
        "epic": _get(content, "Epicnumber") or "N/A",
        "first_name": first_name or "N/A",
        "full_name": full_name or "N/A",
        "gender": gender,
        "last_name": last_name or "N/A",
        "part": part,
        "pc": pc,
        "polling": polling,
        "ps": ps,
        "psn": psn,
        "rel_first": rel_first or "N/A",
        "rel_last": rel_last or "N/A",
        "relative_full_name": relative_full_name or "N/A",
        "state": _get(content, "Statename") or "N/A",
        "voter_with_relative": voter_with_relative,
    }

    # ═══ Extra helpful fields ═══
    district = _get(content, "Districtvalue")
    if district:
        clean["district"] = district

    latlong = _get(content, "Partlatlong", "Part Lat Long")
    if latlong:
        clean["part_latlong"] = latlong

    rel_type_raw = _get(content, "Relationtype")
    if rel_type_raw:
        clean["relation_type"] = rel_type_raw

    return clean


# ==============================================
# ⚡ EXTRACT RECORDS FROM API RESPONSE
# ==============================================

def extract_records(body_text):
    """
    Pull list of raw records from ECI API JSON response.
    Returns (records_list, error_msg)
    """
    if not body_text:
        return [], "Empty response"

    try:
        data = json.loads(body_text)
    except Exception as e:
        # Non-JSON → check for known errors
        low = body_text.lower()
        if "invalid captcha" in low or "enter valid captcha" in low:
            return [], "Invalid captcha"
        if "no data" in low or "not found" in low or "no record" in low:
            return [], "No voter record found"
        return [], f"Invalid JSON response: {str(e)[:80]}"

    # Walk the JSON tree looking for the records list
    def find_records(node, depth=0):
        if depth > 6 or node is None:
            return None

        if isinstance(node, list):
            # check if it's list of records (dicts with content-like keys)
            if node and all(isinstance(x, dict) for x in node):
                # Heuristic: has "content" key OR looks like a record
                if any("content" in x or "Content" in x for x in node):
                    return node
                # Or has Epicnumber-like keys
                if any(
                    any(k in x for k in ("Epicnumber", "epicnumber", "Content", "content"))
                    for x in node
                ):
                    return node
            # recurse into items
            for item in node:
                r = find_records(item, depth + 1)
                if r:
                    return r
            return None

        if isinstance(node, dict):
            # direct hits
            for key in ("records", "result", "content", "Content", "data", "Data",
                        "recordsList", "recordslist", "voters"):
                if key in node:
                    r = find_records(node[key], depth + 1)
                    if r:
                        return r
            # recurse all values
            for v in node.values():
                r = find_records(v, depth + 1)
                if r:
                    return r

        return None

    records = find_records(data)

    if not records:
        # check success flag / message
        if isinstance(data, dict):
            msg = data.get("message") or data.get("Message") or ""
            if msg:
                return [], str(msg)
        return [], "No voter record found"

    return records, None


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
# MAIN SEARCH — API response based (no VD click)
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

            # ═══ Load page ═══
            try:
                await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                await browser.close()
                return {"error": "PAGE_LOAD_FAILED",
                        "message": f"Load failed: {str(e)[:150]}"}

            await page.wait_for_timeout(1000)

            # ═══ EPIC tab ═══
            for sel in ['text=Search by EPIC', 'text=ईपीआईसी द्वारा खोजें',
                        'button:has-text("EPIC")']:
                try:
                    await page.click(sel, timeout=800)
                    break
                except:
                    continue

            await page.wait_for_timeout(300)

            # ═══ Fill EPIC ═══
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

            # ═══ API response hook ═══
            api_resp = {"body": None}

            async def on_resp(resp):
                try:
                    u = resp.url.lower()
                    if "search-by-epic" in u or "searchbyepic" in u:
                        body_text = await resp.text()
                        api_resp["body"] = body_text
                except:
                    pass

            page.on("response", on_resp)

            # ═══ Captcha + Search loop ═══
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

                api_resp["body"] = None
                await page.mouse.click(clicked["x"], clicked["y"])

                # Wait for API response
                got = False
                for _ in range(80):
                    if api_resp["body"]:
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

            # ═══ Parse API response ═══
            records, err = extract_records(api_resp["body"] or "")

            if err and not records:
                await browser.close()
                return {"error": "NO_DATA",
                        "message": err or f"No voter record found for EPIC: {epic}"}

            if not records:
                await browser.close()
                return {"error": "NO_DATA",
                        "message": f"No voter record found for EPIC: {epic}"}

            # ═══ Map ALL records to clean format ═══
            mapped = [map_eci_record(r) for r in records]

            await browser.close()

            return {
                "detail": mapped[0],
                "all_records": mapped,
                "total_records": len(mapped),
                "raw_count": len(records)
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
        "version": "4.0.0",
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
            "total_records": result.get("total_records", 1),
            "all_records": result.get("all_records", [result.get("detail", {})]),
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
    print("🗳️ ECI VOTER INFO API v4.0 (API-RESPONSE BASED)")
    print("=" * 60)
    print(f"🚀 Port: {port}")
    print("🔑 Key: QWM")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
