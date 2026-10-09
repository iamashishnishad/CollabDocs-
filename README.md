# CollabDocs API

Backend API (Django REST Framework + PostgreSQL) for workspaces, role-based collaboration, versioned documents, threaded comments, tags and audit logs.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit DB credentials
createdb collabdocs             # or create the DB however you prefer
python manage.py migrate        # apply migrations
python manage.py runserver      # http://localhost:8000
python manage.py test           # run the test suite (needs DB access to create test_collabdocs)
```

Credentials live only in `.env` (never committed); see `.env.example` for all variables.

## Auth
Token auth. `POST /api/users/` registers, `POST /api/users/login/` returns a token; send `Authorization: Token <token>`.

## Endpoints (all under `/api/`)

| Area | Endpoint |
|---|---|
| Users | `POST users/` (register), `POST users/login/`, `GET users/` (search, ids, joined_after/before), `GET/PUT/DELETE users/{id}/` |
| Workspaces | `POST/GET workspaces/`, `GET/PUT/DELETE workspaces/{id}/`, `GET/POST workspaces/{id}/members/` (409 on duplicate), `GET workspaces/{id}/stats/` |
| Documents | `POST/GET documents/` (`q`, `status`, `tag`, date filters), `GET/PUT/DELETE documents/{id}/`, `GET documents/{id}/versions/`, `GET documents/{id}/summary/`, `GET/POST/DELETE documents/{id}/tags/` |
| Comments | `POST/GET comments/` (`document`, `top_level`, `parent`), `GET/PUT/DELETE comments/{id}/` |
| Tags | `POST/GET tags/`, `GET tags/{id}/` |
| Audit Logs | `GET audit-logs/`, `GET audit-logs/{id}/` (filter `model_name`, `action`, `object_id`, `from`, `to`) |

Roles: `admin` (everything, manage members), `editor` (create/edit documents), `viewer` (read + comment). Non-members get 404 for a workspace's resources.

## Design notes
- **Transactions**: workspace + admin membership (+ optional `invite_user_ids`) + audit log is one `atomic()` block. Send an unknown or duplicated invitee to see the whole thing roll back (404 / 409). Document save + `DocumentVersion` (`versions.count() + 1`) is one `atomic()` block.
- **Signal**: `post_save` on `Document` (`core/signals.py`, wired in `CoreConfig.ready()`) writes an `AuditLog` (actor = `created_by`). Django resets `_state.adding` before `post_save`, so a `pre_save` receiver captures it first.
- **Middleware**: `core.middleware.RequestLoggingMiddleware` prints `METHOD path status time_ms` per request.
- **Errors**: `{"error": "..."}`; `DoesNotExist` → 404, `IntegrityError` → 409, validation → 400 with `details`.

## Postman
Import `CollabDocs.postman_collection.json`, run **Users → Register (Alice, Bob) → Login** first (the login test script stores the token and ids as collection variables), then work top to bottom.

## Demo video
Link: _add Loom / Google Drive URL here_
