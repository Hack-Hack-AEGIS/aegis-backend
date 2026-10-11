class Pause:
    def __init__(self): self.fired = False
    def check(self, level, drivers, at, explanation=None):
        if self.fired or level != "CRITICAL": return None
        self.fired = True
        ev = {"type": "reality_pause", "at": at, "reasons": drivers[:4]}
        # Plain-language alert from aegis-core (EN + HI headline, quoted reasons,
        # advice, next step); the frontend shows it on the Reality Pause card.
        if explanation and explanation.get("level") != "none":
            ev["explanation"] = explanation
        return ev
