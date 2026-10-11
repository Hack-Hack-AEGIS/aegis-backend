import asyncio
import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
import app.library_store as store
from app.session import LiveSession
from app.replay import run_replay

@asynccontextmanager
async def lifespan(app):
    # Load user-added incidents saved by earlier runs into the DNA library.
    store.rebuild()
    yield

app = FastAPI(title="aegis-backend", lifespan=lifespan)

# Shared sessions: the audio client and the dashboard join the same call with
# /ws?session=<id>; every turn's events go to all sockets in that session.
SESSIONS: dict[str, dict] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "extractor": os.getenv("AEGIS_EXTRACTOR", "rules")}

@app.get("/library/incidents")
def get_incidents():
    return store.get_library().frontend_incidents()

@app.post("/incidents")
def post_incidents(incident: dict, background_tasks: BackgroundTasks):
    try:
        new_id = store.add_incident(incident)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    background_tasks.add_task(store.rebuild)
    return {"id": new_id}

@app.post("/library/rebuild")
def rebuild_library(background_tasks: BackgroundTasks):
    background_tasks.add_task(store.rebuild)
    return {"status": "rebuilding"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, replay: str = None, speed: float = 1.0, session: str = None):
    await websocket.accept()
    if replay and not re.match(r"^[\w-]+$", replay):
        await websocket.close(code=1008, reason="Invalid replay name")
        return
    if session and not re.match(r"^[\w-]{1,64}$", session):
        await websocket.close(code=1008, reason="Invalid session id")
        return

    if session:
        shared = SESSIONS.setdefault(session, {"live": LiveSession(store.get_library()), "sockets": set()})
    else:
        shared = {"live": LiveSession(store.get_library()), "sockets": set()}
    shared["sockets"].add(websocket)
    live = shared["live"]

    async def sock_send(msg):
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    async def broadcast(msg):
        for ws in list(shared["sockets"]):
            try:
                await ws.send_json(msg)
            except Exception:
                shared["sockets"].discard(ws)

    replay_task = None
    if replay:
        speed = min(speed, 4.0)
        replay_task = asyncio.create_task(run_replay(broadcast, live, replay, speed))

    try:
        while True:
            data = await websocket.receive_json()
            if "speaker" not in data or data["speaker"] not in ["caller", "user"] or "text" not in data:
                await sock_send({"type": "error", "message": "Invalid turn format"})
                continue
            if len(data["text"]) > 2000:
                await sock_send({"type": "error", "message": "Text too long"})
                continue
                
            events = await live.turn(data)
            for ev in events:
                await broadcast(ev)
    except WebSocketDisconnect:
        if replay_task:
            replay_task.cancel()
    finally:
        shared["sockets"].discard(websocket)
        if session and not shared["sockets"]:
            SESSIONS.pop(session, None)
