from aegis_core.state_machine import PRESSURE
from aegis_core.taxonomy import STAGE_INDEX, Signal, Stage

RANK = ["NORMAL", "WATCH", "INTERVENE", "CRITICAL"]
MONEY = {"CREDENTIALS", "TRANSFER"}
# Asking for an OTP/PIN or a payment while pressuring the user is the last
# moment a warning still helps, so it pauses before the user answers.
ASK = {Signal.CREDENTIAL_REQUEST, Signal.PAYMENT_REQUEST}

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

    # A caller asking for an OTP/PIN or an app install is never normal, even without pressure
    # ("send me your otp"). A pressure cue alone stays quiet: bill reminders say "due today".
    floor = "INTERVENE" if seen & {Signal.CREDENTIAL_REQUEST, Signal.REMOTE_ACCESS} else "NORMAL"
    if not pressure or score < 0.10: level = "NORMAL"
    elif (si >= STAGE_INDEX[Stage.MONEY_MOVEMENT] or (si >= STAGE_INDEX[Stage.COMPLIANCE] and money_next)
          or seen & ASK):
        level = "CRITICAL"
    elif si >= STAGE_INDEX[Stage.ISOLATION] or score >= 0.5: level = "INTERVENE"
    else: level = "WATCH"
    if RANK.index(level) < RANK.index(floor): level = floor
    if RANK.index(level) < RANK.index(prev): level = prev
    return level, d
