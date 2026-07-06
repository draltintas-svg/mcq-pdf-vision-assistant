from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image, ImageOps
import pytesseract
from rapidfuzz import fuzz, process

APP_DIR = Path(__file__).resolve().parent
SEED_PATH = APP_DIR / "seed" / "biology_question_bank_import.json"
OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "eng+deu")
MATCH_THRESHOLD = int(os.getenv("MATCH_THRESHOLD", "48"))

app = FastAPI(title="MCQ FastScan Python-only")
ITEMS: list[dict[str, Any]] = []
CHOICES: list[str] = []
ID_BY_CHOICE: dict[str, int] = {}


def norm(text: str) -> str:
    text = text or ""
    text = text.replace("→", " ").replace("–", "-").replace("—", "-")
    text = re.sub(r"[^A-Za-z0-9µα-ωΑ-Ω₂₃₄⁺+\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def load_items() -> None:
    global ITEMS, CHOICES, ID_BY_CHOICE
    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    items = raw.get("items", raw if isinstance(raw, list) else [])
    enriched: list[dict[str, Any]] = []
    choices: list[str] = []
    id_map: dict[str, int] = {}
    for idx, item in enumerate(items):
        q = str(item.get("question", ""))
        section = str(item.get("section", ""))
        part = str(item.get("part", ""))
        options = item.get("options") or {}
        option_text = " ".join([str(v) for v in options.values()]) if isinstance(options, dict) else ""
        search_text = str(item.get("search_text") or f"{q} {option_text} {section} {part}")
        item["_norm_question"] = norm(q)
        item["_norm_search"] = norm(search_text)
        enriched.append(item)
        # Choice string is intentionally compact for fast RapidFuzz extraction.
        choice = f"{item['_norm_question']} {item['_norm_search']}"
        choices.append(choice)
        id_map[choice] = idx
    ITEMS = enriched
    CHOICES = choices
    ID_BY_CHOICE = id_map


load_items()


def best_match(query: str) -> dict[str, Any]:
    qn = norm(query)
    if not qn:
        return {"found": False, "score": 0, "message": "Kein lesbarer Fragetext erkannt."}

    # First pass: fast global extraction.
    matches = process.extract(qn, CHOICES, scorer=fuzz.token_set_ratio, limit=5)
    best_item: dict[str, Any] | None = None
    best_score = -1
    debug = []
    for choice, score, _ in matches:
        item = ITEMS[ID_BY_CHOICE[choice]]
        # Fine score, balancing question and full search text.
        sq = fuzz.token_set_ratio(qn, item.get("_norm_question", ""))
        ss = fuzz.token_set_ratio(qn, item.get("_norm_search", ""))
        sp = fuzz.partial_ratio(qn, item.get("_norm_search", ""))
        final = round(max(score, sq, ss, sp * 0.92), 1)
        debug.append({"id": item.get("id"), "score": final, "question": item.get("question")})
        if final > best_score:
            best_score = final
            best_item = item

    if not best_item or best_score < MATCH_THRESHOLD:
        return {
            "found": False,
            "score": best_score if best_score > 0 else 0,
            "threshold": MATCH_THRESHOLD,
            "message": "Kein sicherer lokaler Treffer.",
            "ocr_text": query[:800],
            "candidates": debug,
        }

    options = best_item.get("options") or {}
    correct_answers = best_item.get("correct_answers") or []
    correct_answer = best_item.get("correct_answer") or (correct_answers[0] if correct_answers else "")
    answer_text = best_item.get("answer_text") or ""
    answer_large = answer_text
    if isinstance(options, dict) and len(correct_answers) == 1:
        label = str(correct_answers[0])
        if label in options:
            answer_large = f"{label}: {options[label]}"

    return {
        "found": True,
        "score": best_score,
        "threshold": MATCH_THRESHOLD,
        "id": best_item.get("id"),
        "task_type": best_item.get("task_type"),
        "part": best_item.get("part"),
        "section": best_item.get("section"),
        "source_page": best_item.get("source_page"),
        "question": best_item.get("question"),
        "options": options,
        "correct_answer": correct_answer,
        "correct_answers": correct_answers,
        "answer_text": answer_text,
        "answer_large": answer_large,
        "ocr_text": query[:1200],
        "candidates": debug,
    }


def ocr_image(data: bytes) -> str:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    # Improve OCR a bit for phone snapshots/screenshots.
    img = ImageOps.exif_transpose(img)
    max_side = 1800
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    gray = ImageOps.grayscale(img)
    # Light thresholding; keeps text clear without complex deps.
    bw = gray.point(lambda x: 255 if x > 180 else 0)
    try:
        text = pytesseract.image_to_string(bw, lang=OCR_LANGUAGE, config="--psm 6")
    except Exception:
        text = pytesseract.image_to_string(gray, lang="eng", config="--psm 6")
    return text.strip()


@app.get("/health")
def health():
    return {"status": "ok", "items": len(ITEMS), "mode": "python-only-fastscan"}


@app.post("/api/search")
async def api_search(q: str = Form("")):
    return JSONResponse(best_match(q))


@app.post("/api/fast-scan")
async def api_fast_scan(file: UploadFile = File(...)):
    data = await file.read()
    text = ocr_image(data)
    result = best_match(text)
    return JSONResponse(result)


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
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#0b1020;color:#fff}main{max-width:900px;margin:0 auto;padding:24px}.card{background:#151d34;border:1px solid #263252;border-radius:22px;padding:20px;margin:16px 0}textarea{width:100%;min-height:120px;border-radius:14px;border:0;padding:14px;font-size:18px;box-sizing:border-box}button{width:100%;border:0;border-radius:14px;background:#22c55e;color:#06130a;padding:16px;font-size:22px;font-weight:800}a{color:#93c5fd}.answer{font-size:clamp(42px,10vw,96px);font-weight:900;color:#fde047;line-height:1.05;margin-top:12px}.small{color:#cbd5e1;font-size:14px}
</style></head><body><main>
<h1>MCQ FastScan</h1><p>Python-only Version ohne Node/React und ohne OpenAI.</p>
<div class="card"><h2>Schnellmodus</h2><p><a href="/fast">/fast öffnen</a> für iPhone-Kamera-Autoscan.</p></div>
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
<title>FastScan</title>
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;overflow:hidden;background:#030712;color:#fff;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif}.wrap{height:100dvh;display:grid;grid-template-rows:1fr auto}.videoBox{position:relative;overflow:hidden;background:#111827}video{width:100%;height:100%;object-fit:cover}.overlay{position:absolute;inset:0;border:6px solid rgba(250,204,21,.85);margin:12px;border-radius:24px;pointer-events:none}.hint{position:absolute;top:20px;left:20px;right:20px;text-align:center;background:rgba(0,0,0,.55);border-radius:18px;padding:10px 14px;font-weight:800}.answer{min-height:36vh;background:#020617;border-top:2px solid #334155;padding:16px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.label{font-size:clamp(82px,25vw,180px);font-weight:1000;line-height:.9;color:#facc15;text-shadow:0 0 18px rgba(250,204,21,.25)}.txt{font-size:clamp(28px,8vw,60px);font-weight:900;line-height:1.05;margin-top:8px;max-width:100%;overflow-wrap:anywhere}.meta{font-size:16px;color:#cbd5e1;margin-top:8px}.bad .label{color:#fb7185}.start{position:fixed;inset:0;background:#020617;display:flex;align-items:center;justify-content:center;z-index:10;padding:24px}.start button{font-size:28px;font-weight:900;border:0;border-radius:24px;background:#22c55e;color:#04130a;padding:22px 28px;box-shadow:0 10px 40px rgba(34,197,94,.25)}canvas{display:none}.pulse{animation:pulse 1s infinite}@keyframes pulse{50%{opacity:.55}}
</style></head><body>
<div class="wrap"><div class="videoBox"><video id="video" playsinline muted autoplay></video><div class="overlay"></div><div class="hint" id="status">Bereit</div></div><div class="answer" id="ans"><div class="label">–</div><div class="txt">Kamera starten</div><div class="meta">Biology-Fragenbank lokal</div></div></div>
<div class="start" id="start"><button onclick="startCam()">Kamera starten</button></div><canvas id="canvas"></canvas>
<script>
const video=document.getElementById('video'), canvas=document.getElementById('canvas'), ans=document.getElementById('ans'), statusEl=document.getElementById('status');let busy=false,lastText='',lastShown='',timer=null;
async function startCam(){document.getElementById('start').style.display='none';try{const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});video.srcObject=stream;statusEl.textContent='Automatischer Scan läuft';timer=setInterval(capture,3600);setTimeout(capture,1200)}catch(e){statusEl.textContent='Kamera nicht freigegeben';showBad('!', 'Kamera erlauben', 0)}}
async function capture(){if(busy||video.readyState<2)return;busy=true;statusEl.textContent='Scanne…';statusEl.classList.add('pulse');const w=video.videoWidth,h=video.videoHeight;if(!w||!h){busy=false;return}canvas.width=w;canvas.height=h;const ctx=canvas.getContext('2d');ctx.drawImage(video,0,0,w,h);canvas.toBlob(async blob=>{try{const fd=new FormData();fd.append('file',blob,'scan.jpg');const r=await fetch('/api/fast-scan',{method:'POST',body:fd});const j=await r.json();if(j.found){show(j)}else{showBad('?', 'Kein sicherer Treffer', Math.round(j.score||0))}statusEl.textContent='Scan läuft automatisch';}catch(e){statusEl.textContent='Netz/Serverfehler';showBad('!', 'Fehler', 0)}finally{statusEl.classList.remove('pulse');busy=false}},'image/jpeg',0.72)}
function show(j){const text=j.answer_large||j.answer_text||j.correct_answer||'';if(text===lastShown)return;lastShown=text;let label=j.correct_answer||'';let rest=text;if(text.includes(':')){const parts=text.split(':');label=parts.shift().trim();rest=parts.join(':').trim()}ans.className='answer';ans.innerHTML=`<div class="label">${esc(label)}</div><div class="txt">${esc(rest||text)}</div><div class="meta">${Math.round(j.score)}% · ${esc(j.id||'')} · S. ${esc(j.source_page||'')}</div>`}
function showBad(label,text,score){ans.className='answer bad';ans.innerHTML=`<div class="label">${esc(label)}</div><div class="txt">${esc(text)}</div><div class="meta">${score?score+'%':''}</div>`}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]))}
</script></body></html>
"""
