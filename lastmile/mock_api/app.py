from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Terra Range API")


@app.get("/api/v1/fieldwork/health/")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/fieldwork/advisories/")
def advisories(
    status: str | None = None,
    since: str | None = None,
) -> list[dict]:
    return [
        {
            "id": "adv_2026_10_0042",
            "alert_id": "alt_8841",
            "ward_id": "ward_north_horr",
            "severity": "watch",
            "valid_from": "2026-11-03T06:00:00Z",
            "valid_until": "2026-11-17T06:00:00Z",
            "approved_by": "reviewer_ndma_01",
            "approved_at": "2026-11-03T07:12:00Z",
            "content": {
                "en": "Vegetation condition in North Horr ward is below normal.",
                "sw": "Hali ya mimea katika wadi ya North Horr iko chini ya kawaida.",
            },
            "channels_allowed": ["sms", "whatsapp"],
            "contains_restricted_content": False,
        }
    ]


@app.get("/api/v1/fieldwork/recipients/")
def recipients() -> list[dict]:
    return [
        {
            "id": "rcp_0019",
            "name": "Test Officer A",
            "role": "ward_agricultural_officer",
            "ward_id": "ward_north_horr",
            "phone_e164": "+254700000019",
            "language": "sw",
            "preferred_channel": "whatsapp",
            "consents": [
                {
                    "id": "cns_0031",
                    "channel": "sms",
                    "status": "active",
                    "consent_text_version": "v1",
                    "captured_via": "inbound_keyword",
                    "captured_at": "2026-10-20T08:03:11Z",
                    "withdrawn_at": None,
                }
            ],
        }
    ]


@app.post("/api/v1/fieldwork/recipients/")
def create_recipient(payload: dict) -> dict:
    return {"id": "rcp_1001", **payload}


@app.patch("/api/v1/fieldwork/recipients/{recipient_id}/")
def update_recipient(recipient_id: str, payload: dict) -> dict:
    return {"id": recipient_id, **payload}


@app.post("/api/v1/fieldwork/consents/")
def create_consent(payload: dict) -> dict:
    return {"id": "cns_9999", **payload}


@app.post("/api/v1/fieldwork/consents/{consent_id}/withdraw/")
def withdraw_consent(consent_id: str, payload: dict | None = None) -> dict:
    return {"id": consent_id, "status": "withdrawn"}


@app.post("/api/v1/fieldwork/deliveries/")
def create_delivery(payload: dict) -> dict:
    return {"id": "dlv_0001", **payload}


@app.patch("/api/v1/fieldwork/deliveries/{delivery_id}/")
def update_delivery(delivery_id: str, payload: dict) -> dict:
    return {"id": delivery_id, **payload}


@app.post("/api/v1/fieldwork/inbound/")
def inbound(payload: dict) -> dict:
    return {"accepted": True, "payload": payload}


@app.middleware("http")
async def mock_failures(request: Request, call_next):
    fail_header = request.headers.get("X-Mock-Fail")
    if fail_header:
        if fail_header == "500":
            raise HTTPException(status_code=500, detail="mock failure")
        if fail_header == "429":
            raise HTTPException(status_code=429, detail="rate limited")
        if fail_header == "timeout":
            return JSONResponse(status_code=504, content={"detail": "mock timeout"})
    return await call_next(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
