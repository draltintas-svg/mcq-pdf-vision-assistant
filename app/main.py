from __future__ import annotations

import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output
from rapidfuzz import fuzz, process

APP_DIR = Path(__file__).resolve().parent
SEED_PATH = APP_DIR / "seed" / "biology_question_bank_import.json"
OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "eng+deu")
MATCH_THRESHOLD = int(os.getenv("MATCH_THRESHOLD", "56"))
MULTI_MATCH_THRESHOLD = int(os.getenv("MULTI_MATCH_THRESHOLD", str(MATCH_THRESHOLD)))
OCR_MAX_WIDTH = int(os.getenv("OCR_MAX_WIDTH", "1000"))
OCR_PSM = os.getenv("OCR_PSM", "3")
OCR_MIN_CONF = int(os.getenv("OCR_MIN_CONF", "20"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "6"))
WINDOW_MATCH_THRESHOLD = int(os.getenv("WINDOW_MATCH_THRESHOLD", "82"))

app = FastAPI(title="MCQ FastScan MultiQuestion Python-only")

ITEMS: list[dict[str, Any]] = []
CHOICES: list[str] = []
ID_BY_CHOICE: dict[str, int] = {}


def norm(text: str) -> str:
    text = text or ""
    text = text.replace("→", " ").replace("–", "-").replace("—", "-")
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"\b([a-dA-D])\s*\)", r" \1 ", text)
    text = re.sub(r"[^A-Za-z0-9µα-ωΑ-Ω₂₃₄⁺+\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def load_items() -> None:
    global ITEMS, CHOICES, ID_BY_CHOICE
    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    source_items = raw.get("items", raw if isinstance(raw, list) else [])
    enriched: list[dict[str, Any]] = []
    choices: list[str] = []
    id_map: dict[str, int] = {}
    for idx, item in enumerate(source_items):
        q = str(item.get("question", ""))
        section = str(item.get("section", ""))
        part = str(item.get("part", ""))
        options = item.get("options") or {}
        option_text = " ".join(str(v) for v in options.values()) if isinstance(options, dict) else ""
        search_text = str(item.get("search_text") or f"{q} {option_text} {section} {part}")
        item["_norm_question"] = norm(q)
        item["_norm_search"] = norm(search_text)
        # Keep task/section labels low-weight, but helpful for ambiguous entries.
        compact_choice = f"{item['_norm_question']} {norm(option_text)}"
        full_choice = f"{compact_choice} {norm(section)} {norm(part)}"
        item["_choice"] = full_choice
        enriched.append(item)
        choices.append(full_choice)
        id_map[full_choice] = idx
    ITEMS = enriched
    CHOICES = choices
    ID_BY_CHOICE = id_map


load_items()


def answer_payload(item: dict[str, Any], score: float, query: str = "", bbox: dict[str, float] | None = None, position: str = "") -> dict[str, Any]:
    options = item.get("options") or {}
    correct_answers = item.get("correct_answers") or []
    correct_answer = str(item.get("correct_answer") or (correct_answers[0] if correct_answers else ""))
    answer_text = str(item.get("answer_text") or "")
    answer_large = answer_text
    if isinstance(options, dict) and len(correct_answers) == 1:
        label = str(correct_answers[0])
        if label in options:
            answer_large = f"{label}: {options[label]}"
    elif correct_answer:
        answer_large = answer_text or correct_answer

    return {
        "found": True,
        "score": round(float(score), 1),
        "threshold": MULTI_MATCH_THRESHOLD,
        "id": item.get("id"),
        "task_type": item.get("task_type"),
        "part": item.get("part"),
        "section": item.get("section"),
        "source_page": item.get("source_page"),
        "question_number": item.get("question_number"),
        "question": item.get("question"),
        "options": options,
        "correct_answer": correct_answer,
        "correct_answers": correct_answers,
        "answer_text": answer_text,
        "answer_large": answer_large,
        "ocr_text": query[:1200],
        "bbox": bbox or {},
        "position": position,
    }


def best_match(query: str, threshold: int | None = None) -> dict[str, Any]:
    threshold = threshold if threshold is not None else MATCH_THRESHOLD
    qn = norm(query)
    if not qn or len(qn) < 4:
        return {"found": False, "score": 0, "message": "Kein lesbarer Fragetext erkannt."}

    matches = process.extract(qn, CHOICES, scorer=fuzz.token_set_ratio, limit=8)
    best_item: dict[str, Any] | None = None
    best_score = -1.0
    debug = []
    for choice, score, _ in matches:
        item = ITEMS[ID_BY_CHOICE[choice]]
        sq = fuzz.token_set_ratio(qn, item.get("_norm_question", ""))
        ss = fuzz.token_set_ratio(qn, item.get("_norm_search", ""))
        sp = fuzz.partial_ratio(qn, item.get("_norm_search", ""))
        # If OCR captured option text but not full question, the full search score matters more.
        final = max(float(score), float(sq), float(ss), float(sp) * 0.92)
        debug.append({"id": item.get("id"), "score": round(final, 1), "question": item.get("question")})
        if final > best_score:
            best_score = final
            best_item = item

    if not best_item or best_score < threshold:
        return {
            "found": False,
            "score": round(best_score if best_score > 0 else 0, 1),
            "threshold": threshold,
            "message": "Kein sicherer lokaler Treffer.",
            "ocr_text": query[:800],
            "candidates": debug,
        }
    payload = answer_payload(best_item, best_score, query)
    payload["candidates"] = debug
    return payload


@dataclass
class OcrLine:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    conf: float

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def preprocess_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = ImageOps.exif_transpose(img)
    if img.width > OCR_MAX_WIDTH:
        scale = OCR_MAX_WIDTH / float(img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    # Restrict very tall captures; iPhone video frames can be tall and waste OCR time.
    max_height = int(os.getenv("OCR_MAX_HEIGHT", "1700"))
    if img.height > max_height:
        scale = max_height / float(img.height)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.35)
    # Mild sharpening helps with phone camera frames. Avoid hard thresholding because screen photos can be uneven.
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray


def image_to_lines(data: bytes) -> tuple[list[OcrLine], str, tuple[int, int]]:
    img = preprocess_image(data)
    config = f"--oem 1 --psm {OCR_PSM} -c preserve_interword_spaces=1"
    try:
        d = pytesseract.image_to_data(img, lang=OCR_LANGUAGE, config=config, output_type=Output.DICT)
    except Exception:
        d = pytesseract.image_to_data(img, lang="eng", config=config, output_type=Output.DICT)

    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    n = len(d.get("text", []))
    for i in range(n):
        txt = str(d["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(d.get("conf", [0])[i])
        except Exception:
            conf = 0.0
        if conf < OCR_MIN_CONF and not re.search(r"\d+\.|[a-dA-D]\)", txt):
            continue
        key = (int(d.get("block_num", [0])[i]), int(d.get("par_num", [0])[i]), int(d.get("line_num", [0])[i]))
        grouped.setdefault(key, []).append(
            {
                "text": txt,
                "left": int(d["left"][i]),
                "top": int(d["top"][i]),
                "right": int(d["left"][i]) + int(d["width"][i]),
                "bottom": int(d["top"][i]) + int(d["height"][i]),
                "conf": conf,
            }
        )

    lines: list[OcrLine] = []
    for words in grouped.values():
        words.sort(key=lambda w: w["left"])
        text = " ".join(w["text"] for w in words)
        left = min(w["left"] for w in words)
        top = min(w["top"] for w in words)
        right = max(w["right"] for w in words)
        bottom = max(w["bottom"] for w in words)
        conf = sum(w["conf"] for w in words) / max(1, len(words))
        if len(norm(text)) >= 2:
            lines.append(OcrLine(text=text, left=left, top=top, right=right, bottom=bottom, conf=conf))

    lines.sort(key=lambda l: (l.top, l.left))
    full_text = "\n".join(l.text for l in lines)
    return lines, full_text, img.size


QUESTION_START_RE = re.compile(
    r"^\s*(?:"
    r"(?:\d{1,3})[\.|\)]\s+"  # 12. Question
    r"|(?:Task\s+)?(?:I{1,3}|IV|V|VI|VII|VIII|IX|X)\.\d+\.?\s*"  # Task IV.1 or V.3
    r")",
    re.IGNORECASE,
)
OPTION_START_RE = re.compile(r"^\s*[a-dA-D][\)|\.]\s+")


def is_noise_line(s: str) -> bool:
    low = s.strip().lower()
    compact = re.sub(r"[^a-z0-9 ]+", " ", low)
    compact = re.sub(r"\s+", " ", compact).strip()
    if not compact:
        return True
    noise_bits = [
        "choose the correct answer", "right answers are given", "given in bold",
        "section a", "section b", "section c", "section d", "section e",
        "section f", "section g", "section h", "cell biology",
        "biochemistry", "microbiology", "virology", "human anatomy",
    ]
    return any(bit in compact for bit in noise_bits)


def is_question_start(line: str) -> bool:
    s = line.strip()
    low = s.lower()
    if OPTION_START_RE.match(s) or is_noise_line(s):
        return False
    if low.startswith(("note:", "section ", "i. choose", "ii. mark", "iii. match", "iv. label", "v. compare", "vi. find")):
        return False
    if QUESTION_START_RE.match(s):
        return True
    # Common OCR miss: number separated by spaces before dot.
    if re.match(r"^\s*\d{1,3}\s+[A-Z][A-Za-z]", s):
        return True
    # When OCR loses the printed question number, a stem often still ends with ':' or '?'.
    if len(norm(s)) >= 12 and (s.endswith(":") or "?" in s):
        return True
    return False


def is_candidate_question_start(line: str) -> bool:
    s = line.strip()
    low = s.lower()
    if not s or OPTION_START_RE.match(s) or is_noise_line(s):
        return False
    if low.startswith(("note:", "section ", "i. choose", "ii. mark", "iii. match", "iv. label", "v. compare", "vi. find")):
        return False
    n = norm(s)
    if len(n) < 10:
        return False
    if is_question_start(s):
        return True
    words = n.split()
    # Biology MCQ stems are usually 4+ words and options follow on the next lines.
    return len(words) >= 4


def bbox_for(lines: list[OcrLine], img_size: tuple[int, int]) -> dict[str, float]:
    w, h = img_size
    if not lines or w <= 0 or h <= 0:
        return {}
    left = min(l.left for l in lines)
    top = min(l.top for l in lines)
    right = max(l.right for l in lines)
    bottom = max(l.bottom for l in lines)
    pad_x, pad_y = int(w * 0.015), int(h * 0.01)
    left = max(0, left - pad_x)
    right = min(w, right + pad_x)
    top = max(0, top - pad_y)
    bottom = min(h, bottom + pad_y)
    return {
        "x": round(left / w, 4),
        "y": round(top / h, 4),
        "w": round((right - left) / w, 4),
        "h": round((bottom - top) / h, 4),
    }


def position_for(bbox: dict[str, float]) -> str:
    y = float(bbox.get("y", 0)) + float(bbox.get("h", 0)) / 2
    if y < 0.34:
        return "oben"
    if y < 0.67:
        return "mitte"
    return "unten"


def line_blocks(lines: list[OcrLine], img_size: tuple[int, int]) -> list[dict[str, Any]]:
    if not lines:
        return []
    starts = [i for i, line in enumerate(lines) if is_question_start(line.text)]
    blocks: list[dict[str, Any]] = []
    if starts:
        starts.append(len(lines))
        for a, b in zip(starts, starts[1:]):
            chunk = lines[a:b]
            text = "\n".join(l.text for l in chunk).strip()
            if len(norm(text)) < 8:
                continue
            bb = bbox_for(chunk, img_size)
            blocks.append({"text": text, "lines": chunk, "bbox": bb, "position": position_for(bb), "method": "question-number"})
        return blocks

    # Fallback: split visible text into vertical bands when OCR did not identify explicit numbers.
    h = img_size[1]
    for label, y0, y1 in [("oben", 0, h / 3), ("mitte", h / 3, 2 * h / 3), ("unten", 2 * h / 3, h)]:
        chunk = [l for l in lines if y0 <= l.center_y < y1]
        text = "\n".join(l.text for l in chunk).strip()
        if len(norm(text)) >= 15:
            bb = bbox_for(chunk, img_size)
            blocks.append({"text": text, "lines": chunk, "bbox": bb, "position": label, "method": "vertical-band"})
    if blocks:
        return blocks

    # Last fallback: one whole image block.
    text = "\n".join(l.text for l in lines).strip()
    bb = bbox_for(lines, img_size)
    return [{"text": text, "lines": lines, "bbox": bb, "position": position_for(bb), "method": "whole-image"}]


def sliding_blocks(lines: list[OcrLine], img_size: tuple[int, int]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if not lines:
        return blocks
    starts = [i for i, l in enumerate(lines) if is_candidate_question_start(l.text)]
    if not starts:
        return blocks
    start_set = set(starts)
    for i in starts[:80]:
        chunk: list[OcrLine] = []
        # Include the stem and up to the next 4 text lines, but stop at the next likely stem after at least 1 line.
        for j in range(i, min(len(lines), i + 6)):
            if j != i and j in start_set and len(chunk) >= 2:
                break
            chunk.append(lines[j])
            text = "\n".join(l.text for l in chunk).strip()
            if len(norm(text)) >= 14:
                bb = bbox_for(chunk, img_size)
                blocks.append({"text": text, "lines": list(chunk), "bbox": bb, "position": position_for(bb), "method": "sliding-window"})
    return blocks[:260]


def dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Keep the best occurrence per DB item; prevents the same question from showing twice.
    by_id: dict[str, dict[str, Any]] = {}
    for r in results:
        key = str(r.get("id") or r.get("question") or len(by_id))
        if key not in by_id:
            by_id[key] = r
            continue
        old = by_id[key]
        new_score = float(r.get("score", 0))
        old_score = float(old.get("score", 0))
        new_h = float((r.get("bbox") or {}).get("h", 1))
        old_h = float((old.get("bbox") or {}).get("h", 1))
        # Prefer higher score; for ties prefer tighter visual box.
        if new_score > old_score + 1 or (abs(new_score - old_score) <= 1 and new_h < old_h):
            by_id[key] = r
    kept = list(by_id.values())
    kept.sort(key=lambda r: (float((r.get("bbox") or {}).get("y", 0)), -float(r.get("score", 0))))
    return kept[:MAX_RESULTS]


def multi_match_from_lines(lines: list[OcrLine], full_text: str, img_size: tuple[int, int]) -> dict[str, Any]:
    initial_blocks = line_blocks(lines, img_size)
    question_number_blocks = [b for b in initial_blocks if b.get("method") == "question-number"]
    # Sliding windows recover individual questions when Tesseract drops printed numbers.
    blocks = question_number_blocks + sliding_blocks(lines, img_size)
    if not blocks:
        blocks = initial_blocks

    results: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for block in blocks[:280]:
        text = block["text"]
        method = str(block.get("method", ""))
        threshold = MULTI_MATCH_THRESHOLD if method == "question-number" else WINDOW_MATCH_THRESHOLD
        match = best_match(text, threshold=threshold)
        cand = {"position": block["position"], "method": block["method"], "text": text[:300], "score": match.get("score", 0), "found": match.get("found", False)}
        candidates.append(cand)
        if match.get("found"):
            item = next((it for it in ITEMS if it.get("id") == match.get("id")), None)
            if item:
                payload = answer_payload(item, float(match.get("score", 0)), query=text, bbox=block["bbox"], position=block["position"])
                payload["method"] = block["method"]
                results.append(payload)

    # If segmentation failed or all scores were low, try full OCR text as one match.
    if not results and len(norm(full_text)) >= 8:
        whole = best_match(full_text, threshold=max(48, MULTI_MATCH_THRESHOLD - 8))
        if whole.get("found"):
            whole["bbox"] = bbox_for(lines, img_size)
            whole["position"] = position_for(whole["bbox"])
            whole["method"] = "whole-image-fallback"
            results.append(whole)

    results = dedupe_results(results)
    return {
        "found": bool(results),
        "items": results,
        "count": len(results),
        "ocr_text": full_text[:3000],
        "image_size": {"width": img_size[0], "height": img_size[1]},
        "threshold": MULTI_MATCH_THRESHOLD,
        "candidates": candidates[:12],
        # Compatibility fields for old frontend.
        **(results[0] if results else {"score": max([c.get("score", 0) for c in candidates], default=0), "message": "Kein sicherer lokaler Treffer."}),
    }


def ocr_and_match_multi(data: bytes) -> dict[str, Any]:
    start = time.perf_counter()
    lines, full_text, img_size = image_to_lines(data)
    payload = multi_match_from_lines(lines, full_text, img_size)
    payload["duration_ms"] = round((time.perf_counter() - start) * 1000)
    payload["line_count"] = len(lines)
    return payload


@app.get("/health")
def health():
    return {"status": "ok", "items": len(ITEMS), "mode": "python-only-fastscan-multiquestion"}


@app.post("/api/search")
async def api_search(q: str = Form("")):
    return JSONResponse(best_match(q))


@app.post("/api/fast-scan")
async def api_fast_scan(file: UploadFile = File(...)):
    data = await file.read()
    return JSONResponse(ocr_and_match_multi(data))


@app.post("/api/fast-scan-multi")
async def api_fast_scan_multi(file: UploadFile = File(...)):
    data = await file.read()
    return JSONResponse(ocr_and_match_multi(data))


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HOME_HTML)


@app.get("/fast", response_class=HTMLResponse)
def fast():
    return HTMLResponse(FAST_HTML)


HOME_HTML = """
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>MCQ FastScan</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#0b1020;color:#fff}main{max-width:900px;margin:0 auto;padding:24px}.card{background:#151d34;border:1px solid #263252;border-radius:22px;padding:20px;margin:16px 0}textarea{width:100%;min-height:120px;border-radius:14px;border:0;padding:14px;font-size:18px;box-sizing:border-box}button{width:100%;border:0;border-radius:14px;background:#22c55e;color:#06130a;padding:16px;font-size:22px;font-weight:800}a{color:#93c5fd}.answer{font-size:clamp(42px,10vw,96px);font-weight:900;color:#fde047;line-height:1.05;margin-top:12px}.small{color:#cbd5e1;font-size:14px}.grid{display:grid;gap:14px}@media(min-width:700px){.grid{grid-template-columns:1fr 1fr}}
</style></head><body><main>
<h1>MCQ FastScan 2.0</h1><p>Python-only, ohne Node/React/npm/OpenAI. Unterstützt mehrere sichtbare Fragen.</p>
<div class="grid"><div class="card"><h2>iPhone-Schnellmodus</h2><p><a href="/fast">/fast öffnen</a>. Einmal Kamera starten, danach scannt die App automatisch weiter.</p></div><div class="card"><h2>Health</h2><p><a href="/health">/health prüfen</a></p></div></div>
<div class="card"><h2>Textsuche</h2><textarea id="q" placeholder="Frage oder Stichworte einfügen..."></textarea><br><br><button onclick="search()">Lösung anzeigen</button><div id="out"></div></div>
</main><script>
async function search(){const fd=new FormData();fd.append('q',document.getElementById('q').value);const r=await fetch('/api/search',{method:'POST',body:fd});const j=await r.json();render(j)}
function render(j){document.getElementById('out').innerHTML=j.found?`<div class="answer">${esc(j.answer_large||j.answer_text||j.correct_answer)}</div><p class="small">Treffer ${j.score}% · ${esc(j.id||'')}</p><p>${esc(j.question||'')}</p>`:`<p class="answer">?</p><p>${esc(j.message||'Kein Treffer')} (${j.score||0}%)</p>`}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]))}
</script></body></html>
"""


FAST_HTML = """
<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<title>FastScan 2.0</title>
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;overflow:hidden;background:#020617;color:#fff;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;-webkit-user-select:none;user-select:none}.app{height:100dvh;width:100vw;position:relative;background:#020617}.videoBox{position:absolute;inset:0;background:#111827;overflow:hidden}video{width:100%;height:100%;object-fit:cover}.shade{position:absolute;inset:0;background:linear-gradient(to bottom,rgba(0,0,0,.38),rgba(0,0,0,.02) 22%,rgba(0,0,0,.04) 70%,rgba(0,0,0,.78));pointer-events:none}.status{position:absolute;top:calc(env(safe-area-inset-top) + 10px);left:10px;right:10px;text-align:center;background:rgba(2,6,23,.74);border:1px solid rgba(148,163,184,.35);border-radius:16px;padding:8px 10px;font-weight:900;font-size:15px;z-index:5}.results{position:absolute;left:8px;right:8px;bottom:calc(env(safe-area-inset-bottom) + 8px);display:flex;flex-direction:column;gap:8px;z-index:4}.result{background:rgba(2,6,23,.90);border:2px solid rgba(250,204,21,.72);border-radius:18px;padding:8px 10px;display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:center;box-shadow:0 8px 24px rgba(0,0,0,.38)}.result .letter{font-size:clamp(54px,18vw,120px);font-weight:1000;line-height:.88;color:#facc15;text-shadow:0 0 14px rgba(250,204,21,.22);min-width:1.05em;text-align:center}.result .txt{font-size:clamp(22px,6vw,48px);font-weight:950;line-height:1.02;overflow-wrap:anywhere}.result .meta{font-size:13px;color:#cbd5e1;margin-bottom:2px;font-weight:800}.boxTag{position:absolute;z-index:3;background:rgba(250,204,21,.92);color:#111827;border-radius:10px;padding:5px 7px;font-weight:1000;font-size:18px;box-shadow:0 4px 16px rgba(0,0,0,.35)}.focus{position:absolute;inset:10px;border:4px solid rgba(250,204,21,.62);border-radius:22px;pointer-events:none;z-index:2}.start{position:fixed;inset:0;background:#020617;display:flex;align-items:center;justify-content:center;z-index:20;padding:24px}.start button{font-size:28px;font-weight:1000;border:0;border-radius:24px;background:#22c55e;color:#04130a;padding:24px 28px;box-shadow:0 10px 40px rgba(34,197,94,.25)}canvas{display:none}.bad .letter{color:#fb7185}.bad{border-color:rgba(251,113,133,.8)}.pulse{animation:pulse 1s infinite}@keyframes pulse{50%{opacity:.50}}@media(orientation:landscape){.results{left:auto;width:44vw;top:calc(env(safe-area-inset-top) + 56px);bottom:calc(env(safe-area-inset-bottom) + 8px);overflow:hidden}.result .letter{font-size:clamp(48px,10vw,110px)}.result .txt{font-size:clamp(20px,4vw,42px)}}
</style></head><body>
<div class="app"><div class="videoBox"><video id="video" playsinline muted autoplay></video><div class="shade"></div><div class="focus"></div><div class="status" id="status">Bereit</div><div id="tags"></div><div class="results" id="results"><div class="result"><div class="letter">–</div><div><div class="meta">FastScan 2.0</div><div class="txt">Kamera starten</div></div></div></div></div></div>
<div class="start" id="start"><button onclick="startCam()">Kamera starten</button></div><canvas id="canvas"></canvas><canvas id="tiny"></canvas>
<script>
const video=document.getElementById('video'), canvas=document.getElementById('canvas'), tiny=document.getElementById('tiny');
const results=document.getElementById('results'), tags=document.getElementById('tags'), statusEl=document.getElementById('status');
let busy=false, stopped=false, lastHash='', sameCount=0, wakeLock=null, lastGood=[];
const INTERVAL_MS=1100, MAX_W=900, JPEG_Q=0.52;
async function startCam(){document.getElementById('start').style.display='none';try{await keepAwake();const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false});video.srcObject=stream;statusEl.textContent='Automatik läuft · halte Frage(n) ruhig ins Bild';setTimeout(loop,900)}catch(e){statusEl.textContent='Kamera nicht freigegeben';showBad('!', 'Kamera erlauben')}}
async function keepAwake(){try{if('wakeLock' in navigator){wakeLock=await navigator.wakeLock.request('screen')}}catch(e){}}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')keepAwake()});
function frameHash(ctx,w,h){tiny.width=16;tiny.height=12;const t=tiny.getContext('2d',{willReadFrequently:true});t.drawImage(canvas,0,0,w,h,0,0,16,12);const d=t.getImageData(0,0,16,12).data;let s=0;for(let i=0;i<d.length;i+=16){s=(s*31+d[i]+d[i+1]+d[i+2])>>>0}return String(s)}
async function loop(){if(stopped)return;if(!busy)await capture();setTimeout(loop,INTERVAL_MS)}
async function capture(){if(busy||video.readyState<2)return;const vw=video.videoWidth,vh=video.videoHeight;if(!vw||!vh)return;busy=true;let w=vw,h=vh;if(w>MAX_W){h=Math.round(h*MAX_W/w);w=MAX_W}canvas.width=w;canvas.height=h;const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(video,0,0,w,h);const hsh=frameHash(ctx,w,h);if(hsh===lastHash){sameCount++;if(sameCount%3!==0){busy=false;return}}else{sameCount=0;lastHash=hsh}statusEl.textContent='Scanne…';statusEl.classList.add('pulse');canvas.toBlob(async blob=>{try{const fd=new FormData();fd.append('file',blob,'scan.jpg');const r=await fetch('/api/fast-scan-multi',{method:'POST',body:fd});const j=await r.json();if(j.found&&j.items&&j.items.length){lastGood=j.items;showItems(j.items,j.duration_ms||0)}else if(lastGood.length){statusEl.textContent='Kein neuer sicherer Treffer · letzte Antwort bleibt';}else{showBad('?', 'Kein sicherer Treffer');statusEl.textContent='Kein sicherer Treffer'}}catch(e){statusEl.textContent='Serverfehler / Netzwerk';if(!lastGood.length)showBad('!', 'Fehler')}finally{statusEl.classList.remove('pulse');busy=false}},'image/jpeg',JPEG_Q)}
function showItems(items,ms){statusEl.textContent=`${items.length} Treffer · ${ms} ms`;results.innerHTML='';tags.innerHTML='';items.slice(0,4).forEach((it,idx)=>{const p=parseAnswer(it);const row=document.createElement('div');row.className='result';row.innerHTML=`<div class="letter">${esc(p.label)}</div><div><div class="meta">${esc((it.position||posFromBox(it.bbox)||'')+' · '+Math.round(it.score||0)+'% · '+(it.id||''))}</div><div class="txt">${esc(p.text)}</div></div>`;results.appendChild(row);addTag(it,idx,p.label)})}
function addTag(it,idx,label){const b=it.bbox||{};if(b.x==null||b.y==null)return;const tag=document.createElement('div');tag.className='boxTag';tag.textContent=`${it.position||idx+1}: ${label}`;tag.style.left=Math.max(2,Math.min(88,b.x*100))+'%';tag.style.top=Math.max(7,Math.min(80,b.y*100))+'%';tags.appendChild(tag)}
function parseAnswer(it){let text=it.answer_large||it.answer_text||it.correct_answer||'';let label=it.correct_answer||'';let rest=text;if(text.includes(':')){const parts=text.split(':');label=parts.shift().trim();rest=parts.join(':').trim()}return{label:label||text||'?',text:rest||text||'Treffer'}}
function posFromBox(b){if(!b||b.y==null)return '';const y=b.y+(b.h||0)/2;return y<.34?'oben':y<.67?'mitte':'unten'}
function showBad(label,text){results.innerHTML=`<div class="result bad"><div class="letter">${esc(label)}</div><div><div class="meta">FastScan</div><div class="txt">${esc(text)}</div></div></div>`;tags.innerHTML=''}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]))}
</script></body></html>
"""
