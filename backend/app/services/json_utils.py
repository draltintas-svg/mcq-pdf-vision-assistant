import json
import re
from typing import Any


def loads_json_lenient(raw: str) -> Any:
    """Parse JSON from an LLM response, tolerating accidental markdown fences."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        first_obj = text.find("{")
        first_arr = text.find("[")
        starts = [pos for pos in [first_obj, first_arr] if pos != -1]
        if not starts:
            raise
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end <= start:
            raise
        return json.loads(text[start : end + 1])
