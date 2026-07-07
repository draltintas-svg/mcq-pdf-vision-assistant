# MCQ FastScan 2.0 MultiQuestion — Python-only

Diese Version hat **kein Node, kein npm, kein React, kein Vite und kein OpenAI**.
Sie ist für Render stabiler und läuft nur mit Python/FastAPI + Tesseract OCR + lokaler Biology-Fragenbank.

## Wichtigste Routen

- `/fast` — iPhone-Schnellscan mit Auto-Scan und mehreren sichtbaren Fragen
- `/` — einfache Textsuche
- `/health` — Statusprüfung

## Was neu ist

- Kamera läuft nach einmaligem Start automatisch weiter.
- Client verkleinert Frames vor Upload, damit OCR schneller ist.
- Mehrere sichtbare Fragen werden in Blöcke zerlegt.
- Antworten werden groß angezeigt und mit Position markiert: `oben`, `mitte`, `unten`.
- Antwort-Tags erscheinen als Overlay im Kamerabild, z. B. `oben: C`.
- Screen Wake Lock wird versucht, damit der Bildschirm wach bleibt.

## GitHub/Render Deployment

1. ZIP entpacken.
2. Im GitHub-Repository alte Node-Ordner am besten entfernen: `frontend`, `backend`, `docker-compose.yml`.
3. Diese Dateien auf oberster Repo-Ebene hochladen:
   - `app/`
   - `Dockerfile.fullstack`
   - `Dockerfile`
   - `render.yaml`
   - `requirements.txt`
   - `README.md`
   - `.dockerignore`
   - `.gitignore`
4. Commit erstellen.
5. Render: `Manual Deploy` → `Deploy latest commit`.
6. Öffnen: `https://DEIN-LINK.onrender.com/fast`.

## Kontrollpunkt

Im Render-Log darf **nie** mehr stehen:

```text
npm
frontend-build
vite
tsc
```

Wenn das noch auftaucht, baut Render noch alte Dateien.

Die richtige Dockerfile beginnt mit:

```dockerfile
FROM python:3.12-slim
```
