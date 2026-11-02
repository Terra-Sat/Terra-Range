# Messaging: recipient registration, SMS consent and Africa's Talking

SMS only for now. WhatsApp is deferred; the `channel` field and provider interface leave room for it.

## How it works
- A person texts JOIN (or an officer runs `register_recipient`). They get a consent prompt and must reply YES.
- STOP withdraws consent at once. After STOP the only message sent is one confirmation.
- Every decision is written to the append-only `ConsentEvent` ledger (channel, status change, timestamp, policy version, outcome). View it in Django admin (read-only).
- The delivery engine sends alerts only through `apps.messaging.sending.send_advisory(recipient, body, reference=...)`, which returns `SendOutcome(sent, reason, retryable)`. Suppressed messages return `sent=False` with a reason such as `consent_withdrawn`.
- Webhooks are idempotent: a unique key per provider event means duplicates do nothing. Delivery statuses only move forward.

## Local setup
    cd backend
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    python -m pytest                      # no database server, network or credentials needed
    python manage.py makemigrations messaging && python manage.py migrate

Register someone and try the webhook locally (provider defaults to `fake`, so no real SMS is sent):

    python manage.py register_recipient --phone 0712345678 --name "Test" --ward MARSABIT-01
    curl -X POST "http://localhost:8000/api/v1/messaging/webhooks/africastalking/$AT_WEBHOOK_TOKEN/inbound/" \
      -d "id=local-1" -d "from=+254712345678" -d "text=YES" -d "date=2026-11-01 10:00:00"

Run `python manage.py expire_consents` on a schedule (for example hourly) to expire unconfirmed requests.

## Environment variables
- MESSAGING_SMS_PROVIDER: `fake` (default) or `africastalking`. Set it to `africastalking` in any real deployment.
- MESSAGING_CONSENT_POLICY_VERSION: version recorded with each consent (default `v1`). Change it when the consent wording changes.
- MESSAGING_CONSENT_PENDING_TTL_HOURS: hours a YES is accepted after JOIN (default 72).
- AT_USERNAME: `sandbox` for the sandbox, otherwise your app username.
- AT_API_KEY: Africa's Talking API key. Never commit it.
- AT_BASE_URL: `https://api.sandbox.africastalking.com` (default) or `https://api.africastalking.com` for production.
- AT_SENDER_ID: optional sender ID or shortcode.
- AT_WEBHOOK_TOKEN: long random secret used in the callback URLs. If empty, all webhooks are rejected.

## Africa's Talking setup
1. Create a sandbox app and API key in the Africa's Talking dashboard.
2. Set the env vars above, with `MESSAGING_SMS_PROVIDER=africastalking`.
3. Expose your server (for local work, a tunnel such as ngrok) and set the SMS callback URLs to:
   - Incoming messages: `https://<host>/api/v1/messaging/webhooks/africastalking/<AT_WEBHOOK_TOKEN>/inbound/`
   - Delivery reports: `https://<host>/api/v1/messaging/webhooks/africastalking/<AT_WEBHOOK_TOKEN>/delivery/`
4. Africa's Talking SMS callbacks are not signed, so the secret token in the URL is the check. The token appears in access logs, so keep it long and rotate it if it leaks.
5. Confirm field names and status codes against the current Africa's Talking docs and the sandbox before going live.

## Safety rules in the code
- Provider errors are reduced to short codes. Logs never contain API keys, request bodies or full phone numbers (last 3 digits only).
- Inbound events store no message text and no phone numbers.
- Assisted registration never grants consent and never overrides a STOP.

## Known limits
- Phone numbers are stored in plain text (needed to send). Encrypt at rest before handling real data.
- A new consent policy version is recorded but does not force existing recipients to re-consent.
- Webhooks are processed synchronously, which is fine at pilot volume.
- Tests run on SQLite, so row locking is only exercised once the suite runs on PostGIS.
