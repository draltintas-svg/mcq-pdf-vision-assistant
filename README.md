# MCQ FastScan Python-only

This version deliberately has **no Node/React build**. Render builds only Python/FastAPI + Tesseract OCR.

Routes:
- `/` normal search page
- `/fast` iPhone camera scan page
- `/health` health check

The Biology question bank is bundled under `app/seed/biology_question_bank_import.json`.

Deploy on Render:
1. Upload/commit these files to GitHub.
2. In Render: Manual Deploy → Deploy latest commit.
3. Open `https://YOUR-APP.onrender.com/fast`.
