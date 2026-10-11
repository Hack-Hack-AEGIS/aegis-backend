import asyncio
from aegis_core.pipeline import AegisSession
from .graph import Graph
from .risk import assess
from .pause import Pause

class LiveSession:
    def __init__(self, library):
        self.core = AegisSession(library=library)
        self.graph, self.pause = Graph(), Pause()
        self.level, self.last_drivers = "NORMAL", None
        self.lock = asyncio.Lock()

    async def turn(self, t: dict) -> list[dict]:
        async with self.lock:
            return await asyncio.to_thread(self._process, t)

    def _process(self, t):
        t = dict(t)
        if t.get("at") is not None and self.core.events_log and t["at"] <= self.core.last_at:
            t.pop("at")
        events = self.core.feed(t)
        at = max((e["at"] for e in events), default=10) + 10
        events += self.graph.update(self.core, at)
        level, drivers = assess(self.core, self.level)
        if level != self.level or drivers != self.last_drivers:
            events.append({"type": "risk", "at": at + 20, "level": level, "drivers": drivers})
        self.level, self.last_drivers = level, drivers
        pz = None
        if level == "CRITICAL" and not self.pause.fired:
            pz = self.pause.check(level, drivers, at + 30, self.core.analysis().get("explanation"))
        if pz: events.append(pz)
        return sorted(events, key=lambda e: e["at"])
