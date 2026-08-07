# School System

Offline-capable Flask school finance and student management dashboard with:
- Admin and Staff workspaces
- PWA manifest + service worker
- SQLite persistence
- Students, payments, users, backup/restore, audit log
- Profile page

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`

## Demo logins
- Admin username: set with `ADMIN_USERNAME` (defaults to `admin`)
- Admin password: set with `ADMIN_PASSWORD`
- Staff account: `staff` / `SecureStaff!42`

## Deploy
Set `SECRET_KEY` and `ADMIN_PASSWORD` in your environment, then run with gunicorn or your platform's production WSGI server.

## Notes
- This starter is designed to be resilient and offline-friendly, but no app can be guaranteed to have zero crashes in every environment.
- Restore accepts `.db`, `.sqlite`, and `.sqlite3` files.


## PWA note
The service worker is served at `/sw.js` for correct root-scope PWA behavior on Render.
