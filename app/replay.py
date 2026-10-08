import asyncio
import json
from pathlib import Path

async def run_replay(sock_send, session, scenario_name, speed=1.0):
    scenario_path = Path("scenarios") / f"{scenario_name}.json"
    if not scenario_path.exists():
        await sock_send({"type": "error", "message": "Scenario not found"})
        return
        
    data = json.loads(scenario_path.read_text())
    turns = data.get("turns", [])
    previous_at = 0
    for turn in turns:
        at = turn.get("at", previous_at)
        delay = (at - previous_at) / 1000.0 / speed
        if delay > 0:
            await asyncio.sleep(delay)
        
        events = await session.turn(turn)
        for ev in events:
            await sock_send(ev)
        
        previous_at = at
