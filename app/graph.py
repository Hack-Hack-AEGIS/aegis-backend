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
