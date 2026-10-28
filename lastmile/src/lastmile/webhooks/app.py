from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

app = FastAPI(title="LastMile webhook receiver")


@app.get("/webhooks/whatsapp")
def whatsapp_verify(mode: str | None = None, challenge: str | None = None, verify_token: str | None = None):
    if mode == "subscribe" and verify_token:
        return PlainTextResponse(challenge or "")
    return PlainTextResponse("invalid", status_code=403)


@app.post("/webhooks/whatsapp")
async def whatsapp_event(request: Request):
    payload = await request.json()
    return {"received": True, "payload": payload}


@app.post("/webhooks/at/inbound/{token}")
async def at_inbound(token: str, request: Request):
    payload = await request.json()
    return {"received": True, "token": token, "payload": payload}


@app.post("/webhooks/at/delivery/{token}")
async def at_delivery(token: str, request: Request):
    payload = await request.json()
    return {"received": True, "token": token, "payload": payload}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
