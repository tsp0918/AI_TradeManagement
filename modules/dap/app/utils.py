from __future__ import annotations

import json
import hashlib
from typing import Any, Dict


def stable_etag(data: Dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
