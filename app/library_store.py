import json
import threading
from pathlib import Path
from pydantic import BaseModel, ValidationError, Field
from aegis_core.library import Library, default_library
from aegis_core.dna import Incident, Fingerprint
from aegis_core.taxonomy import Step, Signal

_lock = threading.Lock()
_current_library = default_library()

DATA_DIR = Path("data")
USER_INCIDENTS_FILE = DATA_DIR / "user_incidents.json"

class IncidentCreate(BaseModel):
    label: str
    surface: str
    family: str = ""
    steps: list[str]
    tactics: list[str] = Field(default_factory=list)

def get_library() -> Library:
    global _current_library
    return _current_library

def load_extra_incidents() -> list[Incident]:
    if not USER_INCIDENTS_FILE.exists():
        return []
    try:
        data = json.loads(USER_INCIDENTS_FILE.read_text())
        out = []
        for d in data:
            steps = [Step(s) for s in d["steps"]]
            tactics = {Signal(t) for t in d.get("tactics", [])}
            fp = Fingerprint.from_steps(steps, tactics if tactics else None)
            out.append(Incident(id=d["id"], label=d["label"], surface=d["surface"], fingerprint=fp, family=d.get("family", "")))
        return out
    except Exception:
        return []

def add_incident(inc_data: dict) -> str:
    try:
        valid_data = IncidentCreate(**inc_data)
        steps = [Step(s) for s in valid_data.steps]
        tactics = {Signal(t) for t in valid_data.tactics}
    except ValueError as e:
        raise ValueError(str(e))
    
    with _lock:
        DATA_DIR.mkdir(exist_ok=True)
        extra = []
        if USER_INCIDENTS_FILE.exists():
            extra = json.loads(USER_INCIDENTS_FILE.read_text())
        new_id = f"U{len(extra) + 1}"
        new_inc = {
            "id": new_id,
            "label": valid_data.label,
            "surface": valid_data.surface,
            "family": valid_data.family,
            "steps": [s.value for s in steps],
            "tactics": [t.value for t in tactics],
        }
        extra.append(new_inc)
        USER_INCIDENTS_FILE.write_text(json.dumps(extra))
        return new_id

def rebuild():
    global _current_library
    extra_incs = load_extra_incidents()
    base_incs = default_library().incidents
    new_lib = Library(base_incs + extra_incs)
    _current_library = new_lib
