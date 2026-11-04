# Messaging service: registration, consent and SMS

Registers people by SMS, records consent, asks where they are (county down to ward, any depth),
and sends alerts only to people who have said yes. Africa's Talking is the SMS provider.
The delivery engine calls into this service; it never talks to the provider itself.

## 1. What a person experiences

```
person  JOIN                       (or JIUNGE)
service Reply YES to get pasture and water alerts. Reply STOP anytime to opt out.
person  YES
service You are subscribed. Reply HELP for help or STOP to opt out.
service Which county are you in? Reply with a number:  1. Marsabit
person  1
service Which sub county are you in?  1. Laisamis  2. Moyale  3. North Horr  4. Saku ...
person  laisamis                   (a number or the name both work)
service Which ward are you in?  1. Kargi/South Horr  2. Korr/Ngurunit  3. Laisamis ...
person  3
service Thank you. You are registered for Laisamis, Laisamis, Marsabit. Reply STATUS for conditions ...
```

The questions come from the location tree in the database, so they follow whatever depth and
levels you load (section 6). Nothing about "county", "sub-county" or "ward" is hard-coded.

| Person sends | What happens |
|---|---|
| `JOIN` / `JIUNGE` | Registered, consent requested. No alerts until they reply YES. |
| `YES` / `NDIYO` | Consent recorded (time, channel, policy version). Welcome sent, then the location questions. |
| `STOP` / `ACHA` | Consent withdrawn immediately. Applies even mid-conversation. No further advisories. |
| `HELP` / `MSAADA` | Help text. |
| `STATUS` / `HALI` | Latest conditions for their ward. |
| `LOCATION` / `ENEO` | Start the location questions again. The old answer stays until a new one is chosen. |
| a number or a name | Answers the open location question. |
| `MORE` / `BACK` / `0` | Next page of options / one level up / "not listed": keep the area chosen so far. |

Rules that always hold: consent is per channel (an SMS "YES" never grants WhatsApp consent, and the
reverse); STOP always wins; duplicate or out-of-order provider events never cause a second action.

## 2. What is in this app

| Piece | Where |
|---|---|
| Consent state machine and audit trail | `consent.py`, `models.py` (`Consent`, `ConsentEvent`, append-only) |
| Inbound handling (idempotent) | `inbound.py`, `webhooks.py` |
| Location conversation | `location_flow.py`, tree in `models.Location`, loader in `locations_loader.py` |
| Provider interface and adapters | `providers/base.py`, `africastalking.py`, `meta_whatsapp.py`, `fake.py` |
| Sending with the consent gate | `sending.py` (`send_advisory`) |
| For the delivery engine | `recipients.py`, `signals.py` |
| Startup configuration checks | `checks.py` |

## 3. Configuration (where the keys go)

All settings are environment variables. They are read from the **`.env` file in the repository root**
(`Terra-Range/.env`, next to `docker-compose.yml`) or from the process environment. **To change the
Africa's Talking key, edit `AT_API_KEY` in that file and restart the service.** `.env` is git-ignored: never
commit it and never put real values in `.env.example`.

| Variable | Required | Purpose |
|---|---|---|
| `MESSAGING_SMS_PROVIDER` | yes | `africastalking` for real SMS. `fake` sends nothing (development and tests). |
| `AT_USERNAME` | with Africa's Talking | Username of the **live** app (shown at the top of the dashboard). `sandbox` only for sandbox testing. |
| `AT_API_KEY` | with Africa's Talking | API key generated in that same app (Settings, API Key). |
| `AT_BASE_URL` | no | `https://api.africastalking.com` (live, default). Sandbox: `https://api.sandbox.africastalking.com`. |
| `AT_SENDER_ID` | no | Your shortcode or approved sender ID. Leave empty to use Africa's Talking's default sender. Set it to your shortcode so replies reach this service (section 4). |
| `AT_WEBHOOK_TOKEN` | production | Secret in the callback URLs. `openssl rand -hex 32`. At least 32 characters. |
| `MESSAGING_CONSENT_POLICY_VERSION` | no | Recorded with every consent (default `v1`). Change it when the consent wording changes. |
| `MESSAGING_CONSENT_PENDING_TTL_HOURS` | no | How long an unanswered JOIN stays pending (default 72). |
| `MESSAGING_LOCATION_SMS_MAX_CHARS` | no | Length budget for one location question (default 300, about two SMS parts). |
| `MESSAGING_LOCATION_MAX_OPTIONS` | no | Most options per page (default 9, so every answer is one digit). |
| `DJANGO_SECRET_KEY` | production | `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DJANGO_ALLOWED_HOSTS` | production | Public host name(s), comma separated, e.g. `sms.example.org`. |
| `DATABASE_URL` | production | PostgreSQL connection string. PostGIS is not needed. |
| `DJANGO_DEBUG` | no | `false` (default). Never `true` in production. |
| `DJANGO_BEHIND_TLS_PROXY` | no | `true` only when a reverse proxy you control terminates HTTPS and sets `X-Forwarded-Proto`. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | no | `https://sms.example.org`, only needed to use the admin over HTTPS. |
| `META_WHATSAPP_*` | WhatsApp only | See `providers/meta_whatsapp.py`. |

The service refuses to start when `africastalking` is selected but the username, key or webhook token is missing
(`config/wsgi.py` runs `manage.py check` as it boots, so gunicorn exits with the reason), and warns when sandbox and
live settings are mixed. `python manage.py check --deploy` shows the same problems before you start it.

## 4. Africa's Talking setup

1. In the Africa's Talking dashboard open your **live** app (not Sandbox). Note its exact username.
2. Settings, API Key: generate a key **in that app**. It is shown once. Put both values in `.env`.
   A key from another app, or from the sandbox, returns `401 The supplied authentication is invalid`.
3. Check the credentials without sending anything (sends no SMS, costs nothing):
   ```bash
   cd ~/Documents/Terra-Range
   KEY=$(grep '^AT_API_KEY=' .env | cut -d= -f2- | tr -d '\r"'"'"' ')
   USR=$(grep '^AT_USERNAME=' .env | cut -d= -f2- | tr -d '\r ')
   curl -s -w "\nHTTP %{http_code}\n" -H "apiKey: $KEY" -H "Accept: application/json" \
     "https://api.africastalking.com/version1/user?username=$USR"     # expect HTTP 200 and a balance
   ```
4. Send one real SMS (costs a fraction of a shilling):
   ```bash
   cd backend
   DJANGO_SETTINGS_MODULE=config.settings_messaging python manage.py send_test_sms --phone 07XXXXXXXX
   ```
   Output: `accepted, message id ATXid_...` means Africa's Talking took it. On failure the command prints the code and
   Africa's Talking's own message (phone numbers masked): `http_401` wrong username/key pair, `at_405` out of credit,
   `at_402` invalid sender ID, `at_406` number blacklisted.
5. **Callbacks.** Africa's Talking only forwards replies and delivery reports to a public HTTPS address.
   In the dashboard (SMS, Callback URLs) set, using the value of `AT_WEBHOOK_TOKEN`:
   - Incoming messages: `https://<host>/api/v1/messaging/webhooks/africastalking/<AT_WEBHOOK_TOKEN>/inbound/`
   - Delivery reports: `https://<host>/api/v1/messaging/webhooks/africastalking/<AT_WEBHOOK_TOKEN>/delivery/`
6. **Replies need a shortcode or dedicated number.** Africa's Talking delivers a person's reply to your
   callback only if the SMS came from a shortcode or number on your account. Alphanumeric sender IDs are send-only.
   Set `AT_SENDER_ID` to that shortcode so every message comes from it, and people can answer YES and the location
   questions. Without one, outbound SMS works but replies never arrive.
7. Callbacks are not signed by Africa's Talking, so the secret token in the URL is the protection. Wrong token: 403 and
   nothing is processed. Rotate it by changing `AT_WEBHOOK_TOKEN`, restarting, and updating both URLs in the dashboard.

## 5. Starting the messaging service

The service runs on its own: its own settings module (`config.settings_messaging`), only PostgreSQL, none of the
mapping or data-pipeline code.

```bash
# 1. Install (Python 3.11+)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-messaging.txt

# 2. Configure: edit ../.env (section 3). Minimum for production:
#    DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DATABASE_URL, MESSAGING_SMS_PROVIDER=africastalking,
#    AT_USERNAME, AT_API_KEY, AT_WEBHOOK_TOKEN
export DJANGO_SETTINGS_MODULE=config.settings_messaging

# 3. Verify, create tables, load the locations, create an admin login
python manage.py check --deploy
python manage.py migrate
python manage.py load_locations --wards-geojson data/wards.geojson      # section 6
python manage.py createsuperuser
python manage.py collectstatic --noinput                               # admin CSS, served by your proxy

# 4. Run
gunicorn config.wsgi --bind 127.0.0.1:8000 --workers 3 --timeout 30
```

Put a reverse proxy (nginx, Caddy, a cloud load balancer) in front for HTTPS, because Africa's Talking must reach the
callback URLs over HTTPS. Point the load balancer's health check at `GET /api/v1/messaging/health/` (returns
`{"status":"ok"}` when the database answers, nothing else). Logs go to stdout.

**Scheduled job.** Run once an hour (cron or your scheduler) so unanswered JOINs expire:
```bash
DJANGO_SETTINGS_MODULE=config.settings_messaging python manage.py expire_consents
```

**Local development without credentials.** Use the fake provider and a throwaway database:
```bash
export DJANGO_SETTINGS_MODULE=config.settings_messaging DJANGO_DEBUG=true MESSAGING_SMS_PROVIDER=fake
python manage.py migrate && python manage.py load_locations --wards-geojson data/wards.geojson
python manage.py simulate_inbound --phone 0712345678 --text JOIN     # runs the real handler; refuses unless DEBUG=true
python manage.py simulate_inbound --phone 0712345678 --text YES
```
`simulate_inbound` is for development only. Real replies must arrive through the webhook.

Operator commands: `register_recipient` (register someone with a known ward and send the consent request),
`send_test_sms`, `list_locations`, `load_locations`, `expire_consents`.

## 6. The location tree (county down to ward, or any depth)

Each `Location` has a `code`, a `name`, a free-text `level` (`county`, `sub_county`, `ward`, `village`, ...) and an
optional parent. The tree can be up to 8 levels deep. Adding a county, or a level below ward, is a data change: load
the rows and people are asked the new questions. Names must be unique among siblings so people can tell them apart by SMS.

**From the project's `wards.geojson`** (county, sub-county and ward):
```bash
python manage.py load_locations --wards-geojson data/wards.geojson --dry-run   # validate and report only
python manage.py load_locations --wards-geojson data/wards.geojson
python manage.py list_locations --max-depth 1
```
The current file loads 25 locations: 1 county, 4 sub-counties and 20 wards.

**From a CSV** (any levels): columns `code,name,level,parent_code,sort_order`; empty `parent_code` means top level.
```csv
code,name,level,parent_code,sort_order
KE010,Marsabit,county,,0
SC-NH,North Horr,sub_county,KE010,0
W-DUKANA,Dukana,ward,SC-NH,0
```
```bash
python manage.py load_locations --csv locations.csv
```

Loading is safe to repeat: rows are matched by `code`, so names, parents and ordering are updated and nothing is
duplicated. The whole file is validated first (missing parents, loops, duplicate names under one parent, bad codes)
and applied in one transaction, so a bad file changes nothing. `--deactivate-missing` hides locations that are not in
the file from the SMS questions; people already linked to them keep their link.

**Ward codes must match the data layer.** `Recipient.ward_code` is the key that `WardIndicator` (STATUS replies) and the
delivery engine use. For `wards.geojson` the loader uses the feature's `uid` as the ward code, the same field the
pipeline falls back to. Confirm once before going live:
```sql
SELECT DISTINCT ward_code FROM geography_wardindicator LIMIT 5;
```
If those values are not the `uid` values, pass the property that matches with `--ward-code-field`.

If a person's area is not in the tree, they can answer `0` at any level below the top to keep the area chosen so far
(for example the sub-county), and their `ward_code` is set to that level's code.

## 7. Connecting the delivery engine

Import these; do not call the SMS provider directly.

```python
from apps.messaging.recipients import active_recipients
from apps.messaging.sending import send_advisory
from apps.messaging.signals import delivery_status_changed

# Everyone with ACTIVE SMS consent anywhere under Marsabit county (any level works: county, sub-county, ward)
for recipient in active_recipients(location_code="KE010"):
    outcome = send_advisory(recipient, text, reference=f"advisory-42:{recipient.id}")
    # outcome.sent, outcome.reason ("consent_withdrawn", "consent_none", "duplicate_reference", ...), outcome.retryable
```

- `active_recipients(location_code=..., include_descendants=True, language=None, ward_code=None, channel=...)` returns
  only people with active consent on that channel. `include_descendants=False` matches that exact level only.
- `send_advisory` checks consent at the moment of sending, so a STOP that arrives while a batch is queued is respected.
  Passing the same `reference` for the same recipient again never sends a second SMS.
- `delivery_status_changed` fires after a delivery report moves a message forward, with `reference`,
  `provider_message_id`, `status` (`queued`, `submitted`, `sent`, `delivered`, `failed`) and `error_code`:
  ```python
  @receiver(delivery_status_changed)
  def on_status(sender, reference, status, **kwargs): ...
  ```
  A failing receiver is logged and never makes the webhook fail.

## 8. Tests and fixtures

```bash
cd backend && python -m pytest apps/messaging
```
No credentials, network or PostGIS needed: the tests use SQLite, the fake provider (`providers/fake.py`) and recorded
Africa's Talking payloads in `tests/fixtures/`. They cover registration and consent, opt-out suppression, duplicate and
out-of-order events, webhook token and signature checks, the Africa's Talking adapter against mocked responses, the
location conversation and loader, channel isolation, and the startup checks. To test an adapter without live credentials,
construct it with a fake `session` as the tests do.

### Running the tests

Use `config.settings_test`, which `pytest.ini` already selects. If your shell exports `DJANGO_SETTINGS_MODULE` (for example `config.settings_messaging`), it overrides `pytest.ini`, so either `unset DJANGO_SETTINGS_MODULE` or run `python -m pytest apps/messaging --ds=config.settings_test`.

## 9. Security and privacy

- API keys, message text and full phone numbers are never logged or stored in event rows; logs show the last 3 digits.
  Provider errors are reduced to short codes. Diagnostic text shown by `send_test_sms` has phone-like numbers masked.
- The webhook token is part of the URL path. gunicorn's access log is off unless you enable `--access-logfile`, but
  your reverse proxy's access log will record the path: keep it private, or strip the token there.
- Consent records are append-only, per channel, with timestamp, source and policy version. Registering someone on
  their behalf never grants consent and never overrides a STOP.
- Treat any key that has been pasted into chat, email or a ticket as compromised: generate a new one in the dashboard.

## 10. Known limits

- Africa's Talking callbacks are unsigned, so the URL token is the only check (section 4).
- Phone numbers are stored in plain text because they are needed to send. Encrypt at rest before holding real data.
- Only Kenyan mobile numbers are accepted (`+254` 7xx and 1xx).
- A person who starts the location questions and never finishes stays "mid-conversation": a later bare number is read as
  an answer. The confirmation SMS always names the area saved, and `LOCATION` changes it.
- A level with a single option is still asked, so a person outside the loaded area is never silently placed inside it.
- Names with non-ASCII characters make that SMS use 70-character parts instead of 160 (`load_locations` warns).
- A new consent policy version is recorded but does not force existing recipients to re-consent.
- Webhooks are processed synchronously, which is fine at pilot volume.
- WhatsApp: a webhook that batches several messages or status updates processes only the first. Fix before relying on WhatsApp.
- Tests run on SQLite, so row locking is exercised only on PostgreSQL.
