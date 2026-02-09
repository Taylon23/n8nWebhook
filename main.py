from fastapi import FastAPI, Request, HTTPException
import httpx
import time
import json
from typing import Dict, Any

app = FastAPI(title="Webhook Router", version="1.0.0")

# ===== CONFIG =====
N8N_WEBHOOK_URL = "https://prompthub.app.n8n.cloud/webhook/Receber-mensagem"
LOG_TO_CONSOLE = True

# dedupe simples em memória
seen: Dict[str, float] = {}
TTL_SECONDS = 60


def log_payload(data: Any):
    if not LOG_TO_CONSOLE:
        return
    print("\n===== WEBHOOK RECEBIDO =====")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("===== FIM =====\n")


def dedupe_ok(message_id: str) -> bool:
    now = time.time()
    for k, ts in list(seen.items()):
        if now - ts > TTL_SECONDS:
            del seen[k]
    if not message_id:
        return True
    if message_id in seen:
        return False
    seen[message_id] = now
    return True


@app.get("/")
def health():
    return {"ok": True, "service": "router_online"}


@app.post("/")
async def webhook_router(req: Request):
    data = await req.json()
    log_payload(data)

    # anti-loop: se já veio marcado, não reenviar
    if data.get("source") == "n8n":
        return {"ok": True, "routed": False, "reason": "loop_protection"}

    # dedupe por messageId
    message_id = data.get("messageId", "")
    if not dedupe_ok(message_id):
        return {"ok": True, "routed": False, "reason": "duplicate", "messageId": message_id}

    # ===== CONDIÇÃO: enviada e recebida por você mesmo =====
    connected_phone = data.get("connectedPhone")             # seu número da instância
    from_me = data.get("fromMe")                             # true se você enviou
    chat_id = (data.get("chat") or {}).get("id")             # chat destino

    is_self_chat = (from_me is True) and (connected_phone is not None) and (chat_id == connected_phone)

    if not is_self_chat:
        return {
            "ok": True,
            "routed": False,
            "reason": "not_self_chat",
            "fromMe": from_me,
            "connectedPhone": connected_phone,
            "chatId": chat_id,
            "messageId": message_id
        }

    # encaminha pro n8n
    async with httpx.AsyncClient(timeout=15) as client:
        data["source"] = "router"
        try:
            r = await client.post(N8N_WEBHOOK_URL, json=data)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to forward to n8n: {e}")

        if r.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"n8n returned {r.status_code}: {r.text}")

    return {"ok": True, "routed": True, "reason": "self_chat", "messageId": message_id}