import pytest
from fastapi.testclient import TestClient
from app.main import app
import app.library_store as store

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_incidents_api():
    d = {
        "label": "Test",
        "surface": "WhatsApp",
        "steps": ["AUTHORITY", "URGENCY"],
        "tactics": ["AUTHORITY_CLAIM"]
    }
    r = client.post("/incidents", json=d)
    assert r.status_code == 200
    assert "id" in r.json()
    
    d2 = {
        "label": "Test2",
        "surface": "WhatsApp",
        "steps": ["INVALID_STEP"]
    }
    r = client.post("/incidents", json=d2)
    assert r.status_code == 422
    
    r = client.get("/library/incidents")
    assert r.status_code == 200

def test_replay_events():
    with client.websocket_connect("/ws?replay=bank_scam&speed=100") as websocket:
        events = []
        transcript_count = 0
        while transcript_count < 8:
            data = websocket.receive_json()
            events.append(data)
            if data.get("type") == "transcript":
                transcript_count += 1
            
        assert len(events) > 0
        last_at = -1
        risk_levels = []
        has_drivers = True
        reality_pause_count = 0
        pause_at = -1
        transfer_at = -1
        has_pii = False
        pii_targets = ["9876543210", "ravi.k@oksbi", "50100234567812", "Ravi Kumar"]
        
        for ev in events:
            if ev.get("at") is not None:
                assert ev["at"] >= last_at
                last_at = ev["at"]
                
            if ev["type"] == "risk":
                risk_levels.append(ev["level"])
                if "drivers" not in ev:
                    has_drivers = False
                    
            if ev["type"] == "reality_pause":
                reality_pause_count += 1
                pause_at = ev.get("at")
                
            if ev["type"] == "transcript":
                if "transfer your balance" in ev["text"].lower():
                    transfer_at = ev.get("at")
                for pii in pii_targets:
                    if pii.lower() in ev["text"].lower():
                        has_pii = True

            if ev["type"] == "graph_node":
                if ev.get("predicted"):
                    assert ev["id"].startswith("p")
                else:
                    assert ev["id"].startswith("n")

        assert len(risk_levels) > 0
        rank = ["NORMAL", "WATCH", "INTERVENE", "CRITICAL"]
        for i in range(len(risk_levels)-1):
            assert rank.index(risk_levels[i]) <= rank.index(risk_levels[i+1])
            
        assert has_drivers
        assert reality_pause_count == 1
        assert pause_at <= transfer_at
        assert not has_pii

def test_single_turn_normal():
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"speaker": "caller", "text": "Please pay the amount to this account."})
        events = []
        transcript_count = 0
        while transcript_count < 1:
            data = websocket.receive_json()
            events.append(data)
            if data.get("type") == "transcript":
                transcript_count += 1
            
        risk_levels = [ev["level"] for ev in events if ev["type"] == "risk"]
        for level in risk_levels:
            assert level == "NORMAL"

def test_concurrent_turns():
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"speaker": "caller", "text": "First turn", "at": 1000})
        websocket.send_json({"speaker": "caller", "text": "Second turn", "at": 2000})
        
        events = []
        transcript_count = 0
        while transcript_count < 2:
            data = websocket.receive_json()
            events.append(data)
            if data.get("type") == "transcript":
                transcript_count += 1
            
        transcripts = [e for e in events if e["type"] == "transcript"]
        assert len(transcripts) >= 2
        assert transcripts[0]["text"] == "First turn"
        assert transcripts[1]["text"] == "Second turn"

def test_reality_pause_has_explanation():
    with client.websocket_connect("/ws?replay=bank_scam&speed=100") as websocket:
        pause = None
        for _ in range(200):
            data = websocket.receive_json()
            if data.get("type") == "reality_pause":
                pause = data
                break
        assert pause is not None
        ex = pause["explanation"]
        assert ex["headline"]["en"] and ex["headline"]["hi"]
        assert ex["reasons"] and all(r["quote"] for r in ex["reasons"])
        assert ex["next_step"]["en"] and ex["next_step"]["hi"]

def test_shared_session_broadcast():
    with client.websocket_connect("/ws?session=call-1") as audio, \
         client.websocket_connect("/ws?session=call-1") as dashboard:
        audio.send_json({"speaker": "caller", "text": "This is SBI head office, your account will be frozen."})
        for ws in (audio, dashboard):
            seen = []
            while "transcript" not in seen:
                seen.append(ws.receive_json()["type"])

def test_bad_session_id_rejected():
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?session=../x") as ws:
            ws.receive_json()
