# Carzonrent DPDPA Hub

On-premise tool for DPDP Act 2023 compliance: consent management, data principal rights requests, breach register with 72-hour timer, and the ROPA / retention / gap / action registers. Pre-loaded with the findings from the 13 HOD interviews.

## What it does
| Module | Features |
|---|---|
| Consent | Versioned notices built from specific purposes, hosted consent and withdrawal pages, embeddable widget, REST API, append-only hash-chained ledger with tamper check, receipts, CSV export |
| Rights requests | Public form and status page, API intake, due date, identity check, notes, withdraw-all-consent action |
| Breach register | Awareness time, 72-hour timer, Board intimation and report tracking, people-notified tracking, printable Board report draft |
| Registers | ROPA, retention schedule, gap register, actions, vendors and DPAs, departments. Search, filter, CSV import and export |
| Admin | Roles (viewer, manager, dpo, admin), audit log, API keys, settings |

Privacy by design: people are stored in the consent ledger only as a salted hash plus a masked label.

## Quick start (no Docker, SQLite, for a trial)
```
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
ADMIN_PASSWORD='choose-a-strong-password' python manage.py init
python manage.py seed-tracker        # loads the Carzonrent tracker
python manage.py demo                # optional sample data
flask --app 'app:create_app' run -p 8000
```
Open http://localhost:8000 and sign in as `admin`.

## Production (Docker, PostgreSQL)
1. `cp .env.example .env` and fill every value. Generate `SECRET_KEY` with `python -c "import secrets;print(secrets.token_hex(32))"`.
2. `docker compose up -d --build`
3. `docker compose exec app python manage.py seed-tracker`
4. Put nginx or Apache in front with HTTPS (the app listens on 127.0.0.1:8000). Terminate TLS there and pass `X-Forwarded-For`.
5. Back up daily: `docker compose exec db pg_dump -U dpdpa dpdpa > backup.sql`. Test a restore.

The app refuses to start in production with the default `SECRET_KEY`. `SECRET_KEY` is also the salt for the consent ledger's person hashes, so changing it later breaks lookups of existing people. Store it safely.

## Connecting your booking system
See Integration & API inside the app. Create an API key, then `POST /api/v1/consents` when a guest agrees and `GET /api/v1/consents/check` before any optional message.

## Roles
viewer (read), manager (edit registers, handle requests and incidents), dpo (plus delete, publish notices, exports), admin (plus users, keys, settings).

## Tests
`python -m unittest discover -s tests` (20 tests: login, roles, CSRF, lockout, CRUD, import, consent flow and tamper detection, API, requests, incident timer, headers).

## Known limits. Read before go-live
- **Tested:** SQLite, the full test suite, and a visual check of the main screens. **Not tested:** the PostgreSQL path and the Docker build (neither was available where this was built). Run the test suite against a staging PostgreSQL first.
- **No OTP.** The hosted consent page and widget accept any email or mobile typed in, so someone could record consent for another person. For production, record consent from your own signed-in booking flow through the API, or add OTP verification before saving.
- **Single node.** Rate limits and login lockout are in memory per worker. Use the reverse proxy for rate limiting at scale.
- **Not legal advice.** Confirm with Legal: the request response period (default 30 days, a setting), the Board report structure and timings against the final DPDP Rules, your role as fiduciary or processor for client-supplied data, and notice wording and languages.
- No email or SMS sending, no SSO, no file uploads. Add as needed.
