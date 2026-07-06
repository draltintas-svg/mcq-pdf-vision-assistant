# MCQ PDF Vision Assistant

GitHub-fertiges MVP für eine persönliche Multiple-Choice-Fragenbank:

- PDF hochladen und automatisch MC-Fragen extrahieren
- digitale PDFs direkt lesen
- gescannte PDFs per OCR verarbeiten
- Fragen, Antwortoptionen, korrekte Antwort und PDF-Seite lokal in SQLite speichern
- Foto/Screenshot einer Frage hochladen und per Vision-Modell auslesen
- Frage semantisch gegen die lokale Datenbank matchen
- richtige Lösung aus der lokalen Datenbank anzeigen
- nur bei fehlendem oder unvollständigem lokalen Treffer optional Web-Fallback nutzen
- UNKNOWN-Antworten im UI manuell nachtragen

> Wichtig: Das Projekt ist als Lern- und Nachschlagesystem gedacht, nicht als Tool für laufende Prüfungen. Medizinische Antworten sollten immer fachlich kontrolliert werden.

---

## Lokaler Testlink

Nach dem Start mit Docker ist die App hier erreichbar:

```text
http://localhost:5173
```

Die API-Dokumentation ist hier erreichbar:

```text
http://localhost:8000/docs
```

Ein öffentlicher Internet-Link entsteht erst nach Deployment auf einem Host. Dieses Paket enthält zusätzlich `Dockerfile.fullstack` und `render.yaml`, damit Frontend und Backend auch als ein einziger Web-Service deployt werden können.

---

## Architektur

```text
frontend/   React + Vite UI
backend/    FastAPI API
SQLite      lokale Fragenbank
OCR         Tesseract lokal oder OpenAI Vision OCR
OpenAI      Vision, Embeddings, MCQ-Parsing, optional Web-Fallback
```

Der Ablauf:

```text
PDF → Text extrahieren → falls nötig OCR → MCQ-Parsing → Antwortschlüssel erkennen → Embeddings erzeugen → SQLite speichern
Foto/Text → Frage extrahieren → Embedding → lokale Suche → Antwort zeigen → sonst Web-Fallback
```

---

## Voraussetzungen

- Docker + Docker Compose, oder lokal:
  - Python 3.12+
  - Node.js 22+
  - Tesseract OCR, wenn du ohne Docker lokale OCR nutzen willst
- OpenAI API-Key

---

## Start mit Docker

```bash
cp .env.example .env
# OPENAI_API_KEY in .env eintragen

docker compose up --build
```

Danach öffnen:

```text
Frontend: http://localhost:5173
Backend:  http://localhost:8000
API Docs: http://localhost:8000/docs
```

Der Backend-Docker-Container installiert automatisch:

```text
tesseract-ocr
tesseract-ocr-deu
tesseract-ocr-eng
```

Damit funktionieren deutsch- und englischsprachige Scans ohne zusätzliche lokale Installation.

---

## Lokaler Start ohne Docker

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env
# OPENAI_API_KEY in backend/.env eintragen oder als Env-Var setzen
uvicorn app.main:app --reload --port 8000
```

Für OCR ohne Docker muss Tesseract lokal installiert sein.

Ubuntu/Debian:

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng
```

macOS mit Homebrew:

```bash
brew install tesseract tesseract-lang
```

### Frontend

In einem zweiten Terminal:

```bash
cd frontend
npm install
npm run dev
```

---

## OCR-Konfiguration

Siehe `.env.example`.

```env
PDF_OCR_ENABLED=true
PDF_OCR_PROVIDER=tesseract
PDF_OCR_LANGUAGE=deu+eng
PDF_OCR_MIN_CHARS_PER_PAGE=50
PDF_OCR_MAX_PAGES=250
PDF_RENDER_DPI=220
TESSERACT_CONFIG=--psm 3
```

### OCR-Modi

| Wert | Bedeutung |
|---|---|
| `PDF_OCR_PROVIDER=tesseract` | lokale OCR im Backend/Docker-Container |
| `PDF_OCR_PROVIDER=openai` | PDF-Seiten werden als Bilder gerendert und per OpenAI-Vision OCR gelesen |
| `PDF_OCR_PROVIDER=off` | OCR deaktiviert; nur digitaler PDF-Text |

Empfehlung:

- `tesseract` für Datenschutz, Kostenkontrolle und viele gescannte Seiten
- `openai` für schwierige Layouts, schlechte Scans oder wenn Tesseract medizinische Abkürzungen schlecht erkennt

---

## Bedienung

1. PDF hochladen.
2. Warten, bis der Import fertig ist.
3. In der Dokumentenmeldung prüfen, ob OCR genutzt wurde.
4. In der Fragenliste UNKNOWN-Antworten kontrollieren und bei Bedarf ergänzen.
5. Foto der Frage hochladen oder Stichworte eingeben.
6. Ergebnis prüfen:
   - `database`: sichere lokale Antwort
   - `database_incomplete`: Frage lokal gefunden, aber Antwort fehlt
   - `web`: kein sicherer lokaler Treffer, externe Recherche genutzt
   - `no_match`: kein sicherer Treffer und Web-Fallback deaktiviert

---

## API-Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/health` | Healthcheck |
| POST | `/api/upload-pdf` | PDF hochladen, Text/OCR ausführen und Fragen extrahieren |
| GET | `/api/documents` | importierte PDFs anzeigen |
| GET | `/api/questions` | erkannte Fragen anzeigen |
| PUT | `/api/questions/{id}` | Antwort/Erklärung korrigieren |
| POST | `/api/answer` | Foto/Text abfragen |

---

## OpenAI-Konfiguration

```env
OPENAI_API_KEY=sk-your-key-here
OPENAI_TEXT_MODEL=gpt-5.4-mini
OPENAI_VISION_MODEL=gpt-5.4-mini
OPENAI_WEB_MODEL=gpt-5.4-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
MATCH_THRESHOLD=0.78
NEAR_MATCH_THRESHOLD=0.68
```

`MATCH_THRESHOLD` steuert, wie streng der lokale Treffer sein muss. Wenn zu oft falsch gematcht wird: erhöhen, z. B. `0.82`. Wenn zu oft kein Treffer gefunden wird: leicht senken, z. B. `0.74`.

---

## Was dieses MVP gut kann

- digitale PDFs mit sauberem Text
- gescannte PDFs mit lesbarem Drucktext
- klassische MC-Fragen mit A–E-Optionen
- Lösungsschlüssel, wenn er im PDF-Text oder OCR-Text erkannt wird
- Foto/Screenshot mit gut lesbarer Frage
- semantische Suche, auch wenn die Formulierung leicht abweicht

---

## Bekannte Grenzen

- OCR ist abhängig von Scanqualität, Schärfe, Rotation, Kontrast und Layout.
- Chaotische Layouts oder Lösungstabellen können UNKNOWN-Antworten erzeugen.
- Diagramm-/Bildfragen können erkannt werden, sind aber je nach Bildqualität schwieriger.
- Die erste PDF-Verarbeitung kann je nach Umfang, OCR und API-Latenz dauern.
- Externe Recherche ist ein Fallback und sollte nicht als gleichwertig mit deiner geprüften lokalen PDF-Datenbank behandelt werden.

---

## GitHub Upload

```bash
git init
git add .
git commit -m "Initial MCQ PDF Vision Assistant with OCR"
git branch -M main
```

Dann ein leeres GitHub-Repository erstellen und die dort angezeigten Push-Befehle ausführen, oder mit GitHub CLI:

```bash
gh repo create mcq-pdf-vision-assistant --public --source=. --push
```

---

## Deployment-Idee für öffentlichen Link

### Variante A: Ein Container für Frontend + Backend

Für den schnellsten öffentlichen Test ist `Dockerfile.fullstack` gedacht. Es baut zuerst das React-Frontend und kopiert es dann in den FastAPI-Container. FastAPI liefert anschließend die Weboberfläche und die API aus demselben Service aus.

Render kann dafür `render.yaml` verwenden. Nach dem GitHub-Push musst du dort nur den `OPENAI_API_KEY` als geheime Env-Variable setzen. Danach bekommst du von Render eine öffentliche URL.

### Variante B: Getrenntes Frontend und Backend

```text
Frontend → Vercel/Netlify
Backend  → Render/Railway/Fly.io/VPS
Datenbank → SQLite für MVP, später PostgreSQL
```

Beim getrennten Frontend muss `VITE_API_BASE_URL` auf die öffentliche Backend-URL zeigen. Beim Backend muss `APP_ORIGIN` auf die öffentliche Frontend-URL zeigen.

Für medizinische/berufliche Nutzung: keine Patientendaten hochladen, Authentifizierung aktivieren und Speicher-/Löschkonzept ergänzen.

## Kostenloser JSON-Import

Diese Version enthält zusätzlich einen lokalen JSON-Import. Damit kannst du eine strukturierte Fragenbank importieren, ohne OpenAI-API-Guthaben zu verbrauchen.

Ablauf:

1. App öffnen.
2. Bei „Strukturierte JSON-Fragenbank auswählen“ die Datei `biology_question_bank_import.json` hochladen.
3. Danach Fragen per Text oder Foto suchen.

Die Foto-Erkennung nutzt lokal Tesseract-OCR. Der OpenAI-Web-Fallback ist optional und standardmäßig ausgeschaltet.

Erwartetes JSON-Schema:

```json
{
  "schema_version": "1.0",
  "source": {"filename": "..."},
  "items": [
    {
      "id": "I-A-001",
      "task_type": "single_choice",
      "question": "Fragetext",
      "options": {"A": "...", "B": "..."},
      "correct_answer": "A",
      "correct_answers": ["A"],
      "source_page": 6
    }
  ]
}
```

## iPhone-Schnellscan-Modus

Diese Version enthält einen Vollbildmodus für iPhone/Safari:

```text
https://DEINE-RENDER-URL.onrender.com/fast
```

Ablauf:

1. Einmal auf **Kamera starten** tippen. iOS verlangt diesen Start aus Datenschutzgründen.
2. Danach die Kamera auf die Frage halten.
3. Die App nimmt automatisch Kameraframes, lädt sie an das Backend hoch, führt lokale Tesseract-OCR aus und gleicht den Text gegen die lokale Fragenbank ab.
4. Die richtige Antwort erscheint groß im Vollbild.

Die Biology-Fragenbank ist als Seed-Datei unter `backend/app/seed/biology_question_bank_import.json` enthalten und wird beim ersten Start automatisch importiert, wenn die lokale Datenbank leer ist. Wenn dein Repository öffentlich ist, entferne diese Seed-Datei oder stelle das Repository privat.

Der Schnellscan ist als Lern- und Selbsttestmodus gedacht, nicht für die Verwendung in laufenden Prüfungen.
