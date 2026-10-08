class Pause:
    def __init__(self): self.fired = False
    def check(self, level, drivers, at):
        if self.fired or level != "CRITICAL": return None
        self.fired = True
        return {"type": "reality_pause", "at": at, "reasons": drivers[:4]}
