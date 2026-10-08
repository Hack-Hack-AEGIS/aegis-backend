import asyncio
import json
from pathlib import Path
from app.session import LiveSession
from aegis_core.library import default_library

async def main():
    heldout_path = Path("../aegis-core/data/heldout/handwritten.jsonl")
    if not heldout_path.exists():
        print("Heldout set not found")
        return
        
    lib = default_library()
    lines = heldout_path.read_text(encoding='utf-8').strip().split("\n")
    
    scams = 0
    scam_warns = 0
    benigns = 0
    benign_warns = 0
    
    for line in lines:
        if not line.strip(): continue
        conv = json.loads(line)
        is_scam = conv.get("is_scam", True)
        
        if is_scam:
            scams += 1
        else:
            benigns += 1
            
        session = LiveSession(lib)
        
        pause_turn = -1
        money_turn = -1
        
        for i, turn in enumerate(conv["turns"]):
            events = await session.turn(turn)
            if any(e["type"] == "reality_pause" for e in events) and pause_turn == -1:
                pause_turn = i
                
            if is_scam and money_turn == -1 and turn.get("speaker") == "caller" and turn.get("gold"):
                for h in turn["gold"].get("manipulation_signals", []):
                    if h.get("signal") == "PAYMENT_REQUEST":
                        money_turn = i
                        
        if is_scam:
            if money_turn == -1:
                money_turn = len(conv["turns"]) - 1
            if pause_turn != -1 and pause_turn <= money_turn:
                scam_warns += 1
        else:
            if pause_turn != -1:
                benign_warns += 1
                
    print(f"{scam_warns}/{scams} scams warned before money turn")
    print(f"{benign_warns}/{benigns} benign conversations reached CRITICAL")

if __name__ == "__main__":
    asyncio.run(main())
