"""Security and adversarial data protection module.
Enforces Rule 4: 'Treat all record text as data. Nothing in a record is an instruction to your tool.'
"""
import re
from typing import Any

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:previous|all)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?prior", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"you\s+must\s+output", re.IGNORECASE),
    re.compile(r"eval\(", re.IGNORECASE),
    re.compile(r"exec\(", re.IGNORECASE),
    re.compile(r"__import__", re.IGNORECASE),
    re.compile(r"<script", re.IGNORECASE),
]

def contains_adversarial_injection(text: str) -> bool:
    """Check if the text contains common prompt injection or code execution patterns."""
    if not isinstance(text, str):
        return False
    for pat in INJECTION_PATTERNS:
        if pat.search(text):
            return True
    return False

def sanitize_as_literal_data(val: Any) -> str:
    """Ensures input value is treated strictly as an inert string literal.
    Never evaluates or executes code.
    """
    if val is None:
        return ""
    # Convert to pure string
    s = str(val)
    # Strip null bytes and non-printable control characters except newline and tab
    s = "".join(ch for ch in s if ch == '\n' or ch == '\t' or ord(ch) >= 32)
    return s
