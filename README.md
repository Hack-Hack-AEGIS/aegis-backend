# aegis-backend

Backend of **AEGIS** (Adaptive Engine for Graph-based Intelligence & Scam-interruption), RAKSHAM AI Cybersecurity Hackathon, IIT Delhi, PS-02: AI-Driven Scam Pattern Recognition.

> Scammers don't steal money first. They steal the decision.

## 1. Project in brief

**Problem.** Phone and chat scams in India (digital arrest, bank KYC with remote access, UPI refund, lottery fee, task/job, family emergency) change wording, brand, language (English/Hindi/Hinglish) and channel constantly, but the underlying workflow stays the same: authority claim, urgency, isolation, remote access, credentials, money transfer. A scam/not-scam verdict arrives too late, and raw call content is sensitive.

**Solution.** AEGIS does not classify scams. It rebuilds how an attack unfolds and interrupts before the money step:

stream in, remove personal data, extract meaning, rebuild the attack workflow, track stage, match Scam DNA, predict next step, compute explainable risk, intervene with Reality Pause.

## 2. Repositories

| Repo | Role |
|---|---|
| aegis-core | Python. Privacy gate, semantic extraction, Manipulation State Machine, Scam DNA, ShadowPath. **Finished. Do not modify.** |
| aegis-backend | **This repo.** Python/FastAPI. Wraps aegis-core, adds causal graph, risk engine, Reality Pause, incident library API, WebSocket stream. |
| aegis-frontend | Next.js dashboard. Consumes the event stream. |
| aegis-realtime, aegis-data, aegis-docs | Not in this workspace. This backend's WebSocket temporarily plays the realtime role. |

Expected workspace layout:

```
workspace/
  aegis-core/
  aegis-backend/
  aegis-frontend/
```

## 3. Event contract (single interface to the frontend)

Defined in `aegis-frontend/types/events.ts`. Do not change it.

```ts
type Stage = "TRUST" | "AUTHORITY" | "URGENCY" | "ISOLATION" | "COMPLIANCE" | "MONEY_MOVEMENT";
type RiskLevel = "NORMAL" | "WATCH" | "INTERVENE" | "CRITICAL";
type AegisEvent =
  | { type: "transcript"; at: number; speaker: "caller" | "user"; text: string }
  | { type: "signal"; at: number; label: string; severity: "info" | "warn" | "critical"; evidence: string }
  | { type: "stage"; at: number; stage: Stage; progress: number; reason: string }
  | { type: "graph_node"; at: number; id: string; label: string; parent?: string; predicted?: boolean }
  | { type: "scam_dna"; at: number; matchPct: number; pattern: string; incidents: number }
  | { type: "shadowpath"; at: number; next: string; confidence: number; evidence: string[] }
  | { type: "risk"; at: number; level: RiskLevel; drivers: string[] }
  | { type: "privacy"; at: number; detected: number; removed: number; transmitted: number }
  | { type: "reality_pause"; at: number; reasons: string[]; explanation?: Explanation };
```

Conventions: `at` is milliseconds from interaction start and must be monotonic per session. `progress` is 0 to 1. `confidence` and `matchPct` are 0 to 100. `risk` always carries `drivers`. Transcript text is always already redacted. Stage names describe observable communication, never the user's mind.

| Event | Producer |
|---|---|
| transcript, privacy, signal, stage, scam_dna, shadowpath | aegis-core, via `AegisSession.feed()` |
| graph_node, risk, reality_pause | **this backend** |

## 4. aegis-core API used here

```python
from aegis_core.pipeline import AegisSession
from aegis_core.library import Library, default_library
from aegis_core.dna import Fingerprint, Incident
from aegis_core.state_machine import PRESSURE
from aegis_core.taxonomy import STEP_LABEL, STAGE_INDEX, Signal, Stage, Step

sess = AegisSession(library=lib)       # one per call; extractor chosen by env AEGIS_EXTRACTOR (default "rules")
events = sess.feed({"speaker": "caller", "text": "...", "at": 4000})   # list[dict], already redacted
sess.events_log        # list[SemanticEvent]; each has .signals() -> set[Signal]
sess.fingerprint.steps # ordered list[Step], first occurrences
sess.prediction        # Prediction | None  (.next: Step, .confidence: int 0-100)
sess.dna               # DNAMatch | None    (.novel, .match_pct, .pattern)
sess.msm.current       # Stage | None
sess.evidence_score()  # float 0..1
sess.last_at           # last turn timestamp in ms
default_library().frontend_incidents()   # [{id,label,surface,steps[],pattern}]
```

`feed()` is synchronous (~0.3 ms with rules; seconds with LLM extractors). Run it with `asyncio.to_thread`.

## 5. Backend layout

```
aegis-backend/
  app/
    __init__.py
    main.py            FastAPI app: routes + WebSocket
    session.py         LiveSession: wraps AegisSession + graph + risk + pause
    graph.py           graph_node events
    risk.py            risk assessment
    pause.py           Reality Pause trigger
    library_store.py   incident library: load, add, rebuild
    replay.py          timed replay of scenarios
  scenarios/
    bank_scam.json     copy of aegis-core/scenarios/bank_scam.json
  data/
    user_incidents.json   created at runtime; fingerprints only
  tests/
    test_backend.py
  tools/
    heldout_warning.py    measures warn-before-money-turn on the held-out set
  pyproject.toml
  README.md
```

`pyproject.toml`:

```toml
[project]
name = "aegis-backend"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "pydantic>=2.6"]
[project.optional-dependencies]
dev = ["pytest", "httpx", "pytest-asyncio"]
[tool.setuptools.packages.find]
include = ["app*"]
```

aegis-core is installed separately with `pip install -e ../aegis-core`.

## 6. Per-turn pipeline

```
turn -> core.feed(turn)                 core events
     -> graph.update(core, at)          graph_node events
     -> risk.assess(core, prev_level)   risk event only when level or drivers change
     -> pause.check(level, drivers)     reality_pause, at most once per session
     -> sort by `at`, send each as one JSON WebSocket message
```

Rules:
- One `asyncio.Lock` per session so turns are processed strictly in order.
- If a client-supplied `at` is not greater than `core.last_at` (and the session already has turns), drop `at` so core assigns the next one.
- Backend-created events use `at = max(core event at) + 10`, `+20`, `+30` so ordering survives timestamp sorting.

## 7. Module specs

### graph.py

One node per entry in `core.fingerprint.steps`, chained to the previous node. If `core.prediction` exists with `confidence >= 50` and differs from the last emitted prediction, emit a predicted node.

```python
from aegis_core.taxonomy import STEP_LABEL

class Graph:
    def __init__(self):
        self.n = 0
        self.last = None
        self.pred_key = None

    def update(self, core, at):
        out = []
        steps = core.fingerprint.steps
        while self.n < len(steps):
            nid = f"n{self.n + 1}"
            ev = {"type": "graph_node", "at": at, "id": nid, "label": STEP_LABEL[steps[self.n]]}
            if self.last:
                ev["parent"] = self.last
            out.append(ev)
            self.last = nid
            self.n += 1
        p = core.prediction
        key = (self.n, p.next.value) if p else None
        if p and p.confidence >= 50 and key != self.pred_key:
            self.pred_key = key
            ev = {"type": "graph_node", "at": at + 10, "id": f"p{self.n}",
                  "label": STEP_LABEL[p.next], "predicted": True}
            if self.last:
                ev["parent"] = self.last
            out.append(ev)
        return out
```

Omit `parent` when absent (do not send null).

### risk.py

Escalation requires at least one pressure signal. A payment or install request alone must never raise the level above NORMAL. Never downgrade within a session.

```python
from aegis_core.state_machine import PRESSURE
from aegis_core.taxonomy import STAGE_INDEX, Signal, Stage

RANK = ["NORMAL", "WATCH", "INTERVENE", "CRITICAL"]
MONEY = {"CREDENTIALS", "TRANSFER"}

def assess(core, prev="NORMAL"):
    seen = {s for e in core.events_log for s in e.signals()}
    pressure = bool(PRESSURE & seen)
    score = core.evidence_score()
    si = STAGE_INDEX[core.msm.current] if core.msm.current else -1
    p = core.prediction
    money_next = bool(p and p.next.value in MONEY and p.confidence >= 50)

    d = []
    if Signal.AUTHORITY_CLAIM in seen: d.append("Authority claim")
    if seen & {Signal.URGENCY, Signal.THREAT}: d.append("Urgency / threat")
    if seen & {Signal.ISOLATION, Signal.SECRECY}: d.append("Isolation / secrecy")
    if Signal.REMOTE_ACCESS in seen: d.append("Remote-access request")
    if Signal.CREDENTIAL_REQUEST in seen: d.append("Credential request")
    if Signal.PAYMENT_REQUEST in seen: d.append("Payment instruction")
    if core.dna and not core.dna.novel and len(core.fingerprint.steps) >= 2:
        d.append(f"{core.dna.match_pct}% pattern match")
    if money_next: d.append("Financial action approaching")

    if not pressure or score < 0.10: level = "NORMAL"
    elif si >= STAGE_INDEX[Stage.MONEY_MOVEMENT] or (si >= STAGE_INDEX[Stage.COMPLIANCE] and money_next):
        level = "CRITICAL"
    elif si >= STAGE_INDEX[Stage.ISOLATION] or score >= 0.5: level = "INTERVENE"
    else: level = "WATCH"
    if RANK.index(level) < RANK.index(prev): level = prev
    return level, d
```

These thresholds are placeholders. Tune only on synthetic data (`aegis_core.datagen.generator.generate`) or the public dev splits. Never tune on `aegis-core/data/heldout/handwritten.jsonl`.

### pause.py

```python
class Pause:
    def __init__(self): self.fired = False
    def check(self, level, drivers, at):
        if self.fired or level != "CRITICAL": return None
        self.fired = True
        return {"type": "reality_pause", "at": at, "reasons": drivers[:4]}
```

### session.py

```python
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
        at = max(e["at"] for e in events) + 10
        events += self.graph.update(self.core, at)
        level, drivers = assess(self.core, self.level)
        if level != self.level or drivers != self.last_drivers:
            events.append({"type": "risk", "at": at + 20, "level": level, "drivers": drivers})
        self.level, self.last_drivers = level, drivers
        pz = self.pause.check(level, drivers, at + 30)
        if pz: events.append(pz)
        return sorted(events, key=lambda e: e["at"])
```

Emit the first `risk` event on the first turn even if NORMAL (so the banner has drivers state); initialize `last_drivers = None` to guarantee that.

### library_store.py

- Base: `default_library().incidents` (synthetic).
- Extra: incidents added via API, persisted to `data/user_incidents.json` as `{id,label,surface,family,steps,tactics}`. **Fingerprints only. Never store text.**
- `get_library()` returns the current `Library`. `add_incident()` validates then appends. `rebuild()` constructs `Library(base + extra)` in a thread (clustering is O(n^3)) and swaps the reference atomically. New sessions use the new library; running sessions keep theirs.
- Build `Incident(id, label, surface, Fingerprint.from_steps(steps, tactics), family)`.
- Validate `steps` against `aegis_core.taxonomy.Step` values and `tactics` against `Signal` values. Reject unknown values with HTTP 422.

### replay.py

`run_replay(sock_send, session, scenario_name, speed)`: load `scenarios/{name}.json` (name must match `^[\w-]+$`), for each turn sleep `(turn.at - previous_at) / 1000 / speed`, feed it through `session.turn`, send the events. The frontend ignores `at` for timing, so the server must delay in real time. Cancel the task when the socket closes.

### main.py

Routes:

| Route | Behaviour |
|---|---|
| `GET /health` | `{"ok": true, "extractor": <AEGIS_EXTRACTOR>}` |
| `GET /library/incidents` | `frontend_incidents()` of the current library |
| `POST /incidents` | body `{label, surface, family, steps[], tactics[]}`; validate; persist; schedule rebuild; return `{"id": ...}` |
| `POST /library/rebuild` | trigger rebuild in background |
| `WS /ws` | query `replay=<scenario>` and `speed=<float, default 1, max 4>` optional |

WebSocket protocol:
- Client to server: JSON turn `{"speaker": "caller"|"user", "text": str, "at"?: int}`.
- Server to client: one JSON AegisEvent per message.
- Invalid turn: send `{"type": "error", "message": "..."}` and keep the socket open. (The frontend must ignore `error`.)
- Limits: `text` max 2000 chars, max 50 concurrent sessions, replay name regex-checked.
- CORS origin from env `FRONTEND_ORIGIN` (default `http://localhost:3000`).
- Create the library once at startup (lifespan), not per request.

Security: bind to `127.0.0.1` by default. No auth is implemented; add auth before any non-local deployment. Never log message bodies.

## 8. Frontend changes (aegis-frontend)

Make only these changes. Read `node_modules/next/dist/docs/` first.

1. **`hooks/useAegis.ts`**
   - `apply()` for `transcript`: remove the call to `sanitize()` and the privacy overwrite. Add transcript text as received. Keep sanitization only inside `play()` (scripted demo) by sanitizing events before applying them, so the scripted demo behaves as before.
   - `graph_node`: upsert by `id`. When a non-predicted node arrives, remove any node whose id starts with `p`.
   - `error` events: ignore them (extend the switch with a default case).
   - Add `connectLive(url: string)`: calls `reset()`, sets `running: true`, opens a WebSocket, applies each parsed message, sets `running: false` on close. Keep the socket in a module variable and close it in `reset()`.
2. **`app/page.tsx`**: add a "Live (backend)" button next to "Start demo" calling `connectLive(`${process.env.NEXT_PUBLIC_BACKEND_WS}?replay=bank_scam&speed=${speed}`)`. Keep the scripted demo as the fallback. Header label: show "LIVE FROM BACKEND" vs "SIMULATED CALL IN PROGRESS" depending on mode.
3. **`demo/incidents.ts`**: add `"LURE"` and `"TRUST"` to `Step`, `STEP_LABEL` ("Reward lure", "Rapport building") and `STEP_COLOR` (pick two distinct colors).
4. **`app/patterns/page.tsx`**: try `fetch(`${process.env.NEXT_PUBLIC_BACKEND_HTTP}/library/incidents`)` on mount. On success use up to 3 incidents with the most distinct step sequences for Scene 2 and keep `newIncident` from the demo file. On failure fall back to `demo/incidents.ts`. The page must not crash when the backend is down.
5. Create `aegis-frontend/.env.local.example`:
```
   NEXT_PUBLIC_BACKEND_WS=ws://localhost:8000/ws
   NEXT_PUBLIC_BACKEND_HTTP=http://localhost:8000
```

## 9. Run

Terminal 1, backend:

```bash
cd aegis-backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ../aegis-core
pip install -e ".[dev]"
cp ../aegis-core/scenarios/bank_scam.json scenarios/
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal 2, frontend:

```bash
cd aegis-frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Open http://localhost:3000, click **Live (backend)**. Open http://localhost:3000/patterns for Scene 2.

Optional LLM tier (off by default): `AEGIS_EXTRACTOR=ensemble-local` with `AEGIS_LLM_BASE_URL`, `AEGIS_LLM_MODEL` set (see aegis-core README), or `ensemble-claude` with `ANTHROPIC_API_KEY`.

Fine-tuned tier used in our numbers (Ollama model built from the `adapter-v3` release of aegis-core):

```bash
LLAMA_ARG_CACHE_RAM=256 ollama serve &          # cap Ollama's prompt cache, it otherwise grows to GBs
AEGIS_EXTRACTOR=ensemble-local AEGIS_LLM_MODEL=aegis-qwen3b-v3 AEGIS_LLM_FEWSHOT=0 \
  uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Shared call session: the audio client (aegis-realtime) and the dashboard both connect to `/ws?session=<call id>`
(letters, digits, `_` and `-`, up to 64 chars). Turns sent by either socket are processed once and every event
goes to all sockets in that session. Without `session`, each socket gets its own call as before.

User-added incidents are stored in `$AEGIS_DATA_DIR/user_incidents.json` (default `data/`) and loaded on startup.
Tests use a temporary folder.

`reality_pause.explanation` is the plain-language alert from aegis-core: `headline {en, hi}`, `reasons[] {signal, en, hi, quote}`,
`advice[] {en, hi}`, `next_step {en, hi}`. Show the headline, each reason with its quote, and the next step as the main button.

Risk: a credential (OTP/PIN/card) or payment request after a pressure cue is CRITICAL, so the Reality Pause fires
before the user answers. Held-out, rules tier: 4/15 scams paused before the last turn, 0/6 normal calls paused.

## 10. Acceptance tests (`tests/test_backend.py`)

1. `GET /health` returns ok.
2. Replaying `bank_scam` yields events with non-decreasing `at`.
3. Risk levels over the replay never decrease and only use the four allowed values.
4. Every `risk` event has a `drivers` list.
5. Exactly one `reality_pause` is emitted, and it occurs before or at the final transfer turn (the turn containing "transfer your balance").
6. A single turn "Please pay the amount to this account." produces no level above NORMAL.
7. No emitted event contains `9876543210`, `ravi.k@oksbi`, `50100234567812` or `Ravi Kumar`.
8. Predicted graph node (`predicted: true`) uses an id starting with `p`; real nodes use `n`.
9. `POST /incidents` with an unknown step returns 422; with valid data returns an id and a subsequent `GET /library/incidents` still works.
10. Two turns sent concurrently on one session are processed in order.

Expected shape of the bank_scam run (verify, report actual): risk starts at WATCH or NORMAL on the authority turn, reaches INTERVENE by the urgency/channel-shift turn, and CRITICAL with `reality_pause` by the remote-access/OTP turns. If the pause only fires at the transfer turn, report it; do not retune on the held-out set.

## 11. Held-out measurement (`tools/heldout_warning.py`)

For each conversation in `../aegis-core/data/heldout/handwritten.jsonl`, run its turns through `LiveSession` with the rules extractor and record the first turn index where the level reaches CRITICAL (the pause turn). Print:

- scams where pause turn <= `money_turn`, as `x/15`
- benign conversations that reached CRITICAL, as `y/6`

This is a report only. Do not use it to change thresholds. For reference, aegis-core alone warns before the money turn on 6/15 scams with rules and 10/15 with the qwen2.5:3b ensemble.

## 12. Definition of done

- `pytest` green; server starts with no warnings about missing modules.
- Live mode in the browser shows: redacted transcript, signals, stage bar, graph growing with a dashed predicted node that resolves, Scam DNA card, ShadowPath card, risk banner with "why" chips, privacy counts from core, Reality Pause once.
- Scripted demo still works unchanged.
- `/patterns` works with and without the backend running.
- aegis-core has zero diff.

## 13. Known limitations (state them, do not hide them)

- Risk thresholds are hand-set placeholders.
- Sessions live in memory; a restart drops them.
- No authentication.
- The held-out set has 21 conversations; numbers are indicative.
- Regex name redaction misses unusual names.