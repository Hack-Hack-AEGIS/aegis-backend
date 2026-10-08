import asyncio
import os
import re
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
import app.library_store as store
from app.session import LiveSession
from app.replay import run_replay

app = FastAPI(title="aegis-backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    store.get_library()

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
async def websocket_endpoint(websocket: WebSocket, replay: str = None, speed: float = 1.0):
    await websocket.accept()
    if replay and not re.match(r"^[\w-]+$", replay):
        await websocket.close(code=1008, reason="Invalid replay name")
        return
        
    session = LiveSession(store.get_library())
    
    async def sock_send(msg):
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    replay_task = None
    if replay:
        speed = min(speed, 4.0)
        replay_task = asyncio.create_task(run_replay(sock_send, session, replay, speed))

    try:
        while True:
            data = await websocket.receive_json()
            if "speaker" not in data or data["speaker"] not in ["caller", "user"] or "text" not in data:
                await sock_send({"type": "error", "message": "Invalid turn format"})
                continue
            if len(data["text"]) > 2000:
                await sock_send({"type": "error", "message": "Text too long"})
                continue
                
            events = await session.turn(data)
            for ev in events:
                await sock_send(ev)
    except WebSocketDisconnect:
        if replay_task:
            replay_task.cancel()
