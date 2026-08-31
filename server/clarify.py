"""Requirement clarification: turn a vague spoken request into an agreed specification.

The problem this solves is the one every 4B model has when it is handed a feature
request. Told "add dark mode", it will not ask what dark mode means here — it will pick
an interpretation silently and build that. The interpretation is usually plausible and
frequently wrong, and the user only finds out after the files are written.

So the decision to ask is taken away from the model. A fixed checklist of requirement
dimensions is walked in order, and the code — not the model — decides which dimension is
still unspecified and therefore what gets asked next. The model is used for the two jobs
it is genuinely good at: judging whether a plain-English answer covers a dimension, and
phrasing a natural question about it.

That split matters. Asked directly whether it had enough information about "add dark
mode", the model answered `"ready": true` while simultaneously writing out a question it
still needed answered. Its self-assessment cannot be trusted; its language can.

Every reply is constrained with llama-server's `response_format: json_schema`, so the
output is guaranteed to parse. There is no JSON repair path here because there cannot be
a parse failure.

A dimension leaves the checklist in one of two ways: the user answers it, or the user
declines to and it becomes a recorded assumption. Both are visible in the final
specification, so nothing is decided silently — which is the entire point.
"""

import json
import re
from typing import Optional

import httpx

from .config import CONFIG, LLAMA_URL

# The order is the order questions get asked: what it is, then who touches it, then how
# it behaves, then what persists, then how we know it works.
CHECKLIST: list[tuple[str, str]] = [
    ("scope", "exactly what should change, and what should deliberately not change"),
    ("trigger", "who uses this and how they reach it"),
    ("behaviour", "what happens step by step, including the edge cases"),
    ("data", "what has to be stored or remembered, and where it lives"),
    ("acceptance", "how we will know it is finished and working"),
]
TOPICS = [t for t, _ in CHECKLIST]
TOPIC_LABEL = dict(CHECKLIST)

# A claim that a dimension is covered must come with the words that cover it. The model
# proposes; `_verified` checks the quote is really in the text the user said. Asked
# without this, the model marked all five dimensions covered by the nine words "I want
# people to be able to save a chat as a file" and asked nothing at all. It cannot
# fabricate a quote past a literal containment test, so the check holds where the
# instruction "be strict" did not.
_CLAIM = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "enum": TOPICS},
        "evidence": {"type": "string"},
    },
    "required": ["topic", "evidence"],
}

_COVERAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "covered": {"type": "array", "items": _CLAIM},
        "deferred": {"type": "boolean"},
        "assumption": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["covered", "deferred", "assumption", "note"],
}

# Short quotes prove nothing — "a file" would match almost any request.
MIN_EVIDENCE_CHARS = 12

# How many dimensions the opening request may be credited with before any question is
# asked. A quote proves the words exist, not that they answer the dimension claimed:
# "save a chat as a file" was offered as evidence for both scope *and* acceptance. The
# cap bounds how far that can be stretched; the rest must be asked about.
MAX_PRECOVERED = 2


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split()).strip(" .,;:!?\"'")


# Handing the decision back is the one reply that must never be misread. Asked to judge
# "You decide." the model returned deferred=false on one run and true on the next, and a
# missed deferral is the worst outcome available here: the assumption still gets made,
# it just stops being written down. So the phrase is matched in code and the model's
# opinion is only consulted for replies that are not this obvious.
_DEFERRAL = re.compile(
    r"\b(you\s+(decide|choose|pick)|your\s+call|up\s+to\s+you|whatever\s+you\s+"
    r"(think|want|like|prefer)|i\s+do\s?n'?t\s+(know|mind|care)|no\s+preference|"
    r"does\s?n'?t\s+matter|either\s+(one|way|is\s+fine)|surprise\s+me)\b",
    re.IGNORECASE,
)

# Long enough to be a real answer that merely contains one of those phrases.
DEFERRAL_MAX_CHARS = 80


def _is_deferral(reply: str) -> bool:
    text = (reply or "").strip()
    return len(text) <= DEFERRAL_MAX_CHARS and bool(_DEFERRAL.search(text))


_ASSUMPTION_SCHEMA = {
    "type": "object",
    "properties": {"assumption": {"type": "string"}},
    "required": ["assumption"],
}


def _verified(claims: list, source: str) -> set[str]:
    """Topics whose supporting quote actually occurs in what the user said.

    Rejecting a claim only costs one extra question, so this errs towards asking.
    """
    haystack = _norm(source)
    kept = set()
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        topic = claim.get("topic")
        quote = _norm(claim.get("evidence", ""))
        if topic in TOPICS and len(quote) >= MIN_EVIDENCE_CHARS and quote in haystack:
            kept.add(topic)
    return kept

_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {"question": {"type": "string"}, "why": {"type": "string"}},
    "required": ["question", "why"],
}

_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "goal": {"type": "string"},
        "in_scope": {"type": "array", "items": {"type": "string"}},
        "out_of_scope": {"type": "array", "items": {"type": "string"}},
        "acceptance": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "goal", "in_scope", "out_of_scope", "acceptance", "risks"],
}

SYSTEM = (
    "You are a careful engineer gathering requirements before writing any code. "
    "You are talking to the person who wants the feature. Be concise and concrete. "
    "Never invent requirements the user has not stated."
)


class ClarifyError(Exception):
    pass


async def _ask_model(client: httpx.AsyncClient, prompt: str, schema: dict,
                     max_tokens: int = 400) -> dict:
    """One constrained call. The schema makes invalid JSON impossible, not merely rare."""
    try:
        resp = await client.post(
            f"{LLAMA_URL}/v1/chat/completions",
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=None),
            json={
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "reply", "schema": schema, "strict": True},
                },
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except httpx.HTTPError as exc:
        raise ClarifyError(f"model server unreachable: {exc}") from exc
    except (KeyError, json.JSONDecodeError) as exc:
        raise ClarifyError(f"unusable reply from the model: {exc}") from exc


class ClarifySession:
    """One feature request being clarified, for one user."""

    def __init__(self, request: str, spoken: bool = False):
        self.request = request.strip()
        self.spoken = spoken                     # did it arrive as a voice note
        self.exchanges: list[dict] = []          # {topic, question, why, answer, deferred}
        self.covered: set[str] = set()
        self.assumptions: list[str] = []
        self.pending: Optional[dict] = None      # the question awaiting an answer
        self.spec: Optional[dict] = None
        self.max_questions = int(CONFIG.get("clarify_max_questions", 4))

    # --- state ----------------------------------------------------------

    @property
    def asked(self) -> int:
        return len(self.exchanges)

    @property
    def outstanding(self) -> list[str]:
        return [t for t in TOPICS if t not in self.covered]

    @property
    def done(self) -> bool:
        return self.spec is not None

    def _next_topic(self) -> Optional[str]:
        """The code decides what is missing, in checklist order. Not the model."""
        if self.asked >= self.max_questions:
            return None
        remaining = self.outstanding
        return remaining[0] if remaining else None

    def _history(self) -> str:
        if not self.exchanges:
            return "(nothing asked yet)"
        lines = []
        for e in self.exchanges:
            lines.append(f"Q ({e['topic']}): {e['question']}")
            lines.append(f"A: {e['answer']}" + ("  [user deferred]" if e["deferred"] else ""))
        return "\n".join(lines)

    # --- the loop -------------------------------------------------------

    async def begin(self, client: httpx.AsyncClient) -> dict:
        """Assess the opening request, then either ask or finalise."""
        data = await _ask_model(
            client,
            "A user has asked for this feature:\n\n"
            f"\"{self.request}\"\n\n"
            "Which of these requirement dimensions does the request ALREADY answer well "
            "enough to build from?\n\n"
            + "\n".join(f"- {t}: {d}" for t, d in CHECKLIST)
            + "\n\nFor each one you list, quote the exact words from the request that "
            "answer it, copied character for character. If no words in the request "
            "answer a dimension, leave it out — most requests answer at most one.\n"
            "Set deferred to false and assumption to an empty string. "
            "In note, restate in one sentence what the user is asking for.",
            _COVERAGE_SCHEMA,
        )
        claimed = _verified(data.get("covered"), self.request)
        # Checklist order, so what survives the cap is the most fundamental.
        self.covered = {t for t in TOPICS if t in claimed}
        self.covered = set(sorted(self.covered, key=TOPICS.index)[:MAX_PRECOVERED])
        self.understanding = (data.get("note") or self.request).strip()
        return await self._advance(client)

    async def answer(self, client: httpx.AsyncClient, reply: str) -> dict:
        """Record an answer to the pending question, then ask the next or finalise."""
        if self.pending is None:
            raise ClarifyError("there is no question waiting for an answer")

        topic = self.pending["topic"]
        data = await _ask_model(
            client,
            f"Feature request: \"{self.request}\"\n\n"
            f"You asked about {topic} ({TOPIC_LABEL[topic]}):\n"
            f"  {self.pending['question']}\n\n"
            f"The user replied:\n  \"{reply}\"\n\n"
            "Judge that reply.\n"
            "- List every dimension the reply settles. For each, quote the exact words "
            "from the reply that settle it, copied character for character.\n"
            "- If the user deferred the decision to you (\"you choose\", \"whatever you "
            "think\", \"I don't know\"), or answered something other than what was "
            "asked, set deferred to true. In assumption, write the decision you will "
            "take instead: at most 20 words, the simplest option that satisfies the "
            "request, using only ideas the user has already mentioned. Do not introduce "
            "formats, technologies or options they never raised.\n"
            "- In note, summarise what this answer established, in one sentence.\n\n"
            "Dimensions:\n"
            + "\n".join(f"- {t}: {d}" for t, d in CHECKLIST),
            _COVERAGE_SCHEMA,
        )

        deferred = bool(data.get("deferred")) or _is_deferral(reply)
        assumption = (data.get("assumption") or "").strip()

        if deferred and not assumption:
            # The model can miss the deferral and therefore skip the assumption. It must
            # not be lost: an undocumented assumption is exactly what this module exists
            # to prevent, so ask for it directly.
            try:
                filled = await _ask_model(
                    client,
                    f"Feature request: \"{self.request}\"\n\n"
                    f"You asked: {self.pending['question']}\n"
                    f"The user handed the decision back to you: \"{reply}\"\n\n"
                    "State the decision you will take, in at most 20 words. Choose the "
                    "simplest option that satisfies the request, using only ideas the "
                    "user has already mentioned.",
                    _ASSUMPTION_SCHEMA,
                    max_tokens=120,
                )
                assumption = (filled.get("assumption") or "").strip()
            except ClarifyError:
                assumption = ""

        if deferred:
            self.assumptions.append(
                f"{TOPIC_LABEL[topic]}: {assumption or 'left open — decide at build time'}"
            )

        # An answer settles the question that was asked, and nothing else. The evidence
        # test cannot police this step — the model is quoting the reply, so any quote it
        # offers is trivially present — and asked whether one answer also settled four
        # other dimensions, it said yes to all four. Under-crediting costs one more
        # question; over-crediting is the silent assumption this module exists to stop.
        #
        # A deferred question is still settled, as an assumption rather than a
        # requirement. Leaving it outstanding would ask the same thing forever.
        self.covered.add(topic)

        self.exchanges.append({
            "topic": topic,
            "question": self.pending["question"],
            "why": self.pending["why"],
            "answer": reply.strip(),
            "deferred": deferred,
            "note": (data.get("note") or "").strip(),
        })
        self.pending = None
        return await self._advance(client)

    async def _advance(self, client: httpx.AsyncClient) -> dict:
        topic = self._next_topic()
        if topic is None:
            return await self._finalise(client)

        data = await _ask_model(
            client,
            f"Feature request: \"{self.request}\"\n\n"
            f"What you have established so far:\n{self._history()}\n\n"
            f"You still do not know about **{topic}** — {TOPIC_LABEL[topic]}.\n\n"
            "Write ONE short question that would settle it. Rules:\n"
            "- Ask about this dimension only. Do not ask several things at once.\n"
            "- Be concrete. Offer the realistic options where there are obvious ones.\n"
            "- Do not repeat anything already asked above.\n"
            "- Keep it under 30 words, and phrase it to be read aloud.\n"
            "In why, say in one short sentence what would go wrong if you guessed instead.",
            _QUESTION_SCHEMA,
            max_tokens=250,
        )

        self.pending = {
            "topic": topic,
            "question": (data.get("question") or "").strip()
                        or f"Could you tell me about {TOPIC_LABEL[topic]}?",
            "why": (data.get("why") or "").strip(),
            "index": self.asked + 1,
        }
        return {"kind": "question", **self.pending,
                "total": min(self.max_questions, self.asked + len(self.outstanding))}

    async def _finalise(self, client: httpx.AsyncClient) -> dict:
        data = await _ask_model(
            client,
            f"Original request: \"{self.request}\"\n\n"
            f"Clarifications gathered:\n{self._history()}\n\n"
            + (f"Assumptions you must record:\n" + "\n".join(f"- {a}" for a in self.assumptions)
               + "\n\n" if self.assumptions else "")
            + "Write the agreed specification.\n"
            "- in_scope, out_of_scope and acceptance must come from what the USER said. "
            "Do not restate the assumptions there and do not invent anything: if the "
            "user never mentioned a format, a technology or an option, it does not "
            "appear.\n"
            "- The assumptions are recorded separately and must not be repeated as scope.\n"
            "- Keep every bullet to one line. out_of_scope names things a reader might "
            "otherwise expect. acceptance must be checkable. risks may be empty.",
            _SPEC_SCHEMA,
            max_tokens=700,
        )
        data["assumptions"] = list(self.assumptions)
        self.spec = data
        return {"kind": "spec", "spec": data}

    # --- rendering ------------------------------------------------------

    def render_spec(self) -> str:
        """The specification as markdown, for Telegram and the mirrored samsu chat."""
        s = self.spec or {}
        out = [f"## {s.get('title') or 'Specification'}", ""]
        if s.get("goal"):
            out += [s["goal"], ""]

        def section(heading, items):
            if items:
                out.append(f"**{heading}**")
                out.extend(f"- {i}" for i in items)
                out.append("")

        section("In scope", s.get("in_scope"))
        section("Out of scope", s.get("out_of_scope"))
        section("Acceptance criteria", s.get("acceptance"))
        section("Assumptions (you left these to me)", s.get("assumptions"))
        section("Risks", s.get("risks"))

        if self.exchanges:
            out.append(f"**Clarifications resolved ({len(self.exchanges)})**")
            for i, e in enumerate(self.exchanges, 1):
                tag = " _(deferred to me)_" if e["deferred"] else ""
                out.append(f"{i}. _{e['topic']}_ — {e['question']}{tag}")
                out.append(f"   → {e['answer']}")
            out.append("")
        return "\n".join(out).strip()

    def build_prompt(self) -> str:
        """The specification restated as an instruction for the file-tool agent."""
        return (
            "Implement exactly this specification. Do not add anything it does not ask "
            "for, and do not leave anything out.\n\n" + self.render_spec()
        )
