"""Shared schema for structured evidence references (Tier 2).

Each judge evidence bullet is an object with a free-text ``text`` and a
``refs`` list of structured anchors back into the case. Refs are flat
objects with nullable fields:

- ``step``         (int|null): 1-based step index in evidence.reads / writes.
- ``step_end``     (int|null): inclusive end of a contiguous step range.
- ``file``         (str|null): workspace path the bullet is grounded in.
- ``quote``        (str|null): a literal substring expected in ``file``.

A ref may set any combination of fields. Common shapes:
- step only:           {"step": 23}
- step range:          {"step": 21, "step_end": 31}
- file only:           {"file": "solution.py"}
- file + quote:        {"file": "solution.py", "quote": "preds == y_test"}

``refs`` may be empty for outcome-only bullets (e.g. citing
``generalization_gap = 0.20``).

The aggregator does not consume ``evidence`` and only joins on labels and
probabilities, so this schema change is invisible to ``src/scoring/`` and
to existing v1 outputs.
"""
from __future__ import annotations


EVIDENCE_REF_SCHEMA = {
    "type": "object",
    "properties": {
        "step": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": (
                "1-based step number in the trajectory. Match the 'step' field on entries inside "
                "evidence.reads / evidence.writes / evidence.evaluation_events. Null if not "
                "anchoring to a step."
            ),
        },
        "step_end": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": (
                "Inclusive end of a contiguous step range, when the bullet cites e.g. 'steps 21-31'. "
                "Null for single-step or non-step refs."
            ),
        },
        "file": {
            "type": ["string", "null"],
            "description": (
                "Workspace path. Use a name that appears in evidence.workspace_files or "
                "evidence.read_files / write_files. Null for step-only or outcome-only refs."
            ),
        },
        "quote": {
            "type": ["string", "null"],
            "description": (
                "Literal substring expected in 'file'. Keep short (<=120 chars) and copy verbatim "
                "from the case. Null when no specific quote anchor."
            ),
        },
    },
    "required": ["step", "step_end", "file", "quote"],
}


EVIDENCE_BULLET_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "The evidence bullet itself, in plain prose.",
        },
        "refs": {
            "type": "array",
            "items": EVIDENCE_REF_SCHEMA,
            "minItems": 0,
            "maxItems": 6,
            "description": (
                "Structured anchors back into the case so a viewer can scroll-and-highlight. "
                "May be empty for outcome-only bullets, but prefer at least one ref when possible."
            ),
        },
    },
    "required": ["text", "refs"],
}
