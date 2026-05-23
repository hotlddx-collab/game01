"""加载 ../data/animals/*.json 形成 persona 字典。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


# 项目根 = agent_server/.. = repo 根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANIMALS_DIR = PROJECT_ROOT / "data" / "animals"


def load_all_personas() -> Dict[str, Dict[str, Any]]:
    """返回 {animal_id: persona_dict}。"""
    result: Dict[str, Dict[str, Any]] = {}
    if not ANIMALS_DIR.exists():
        raise FileNotFoundError(f"找不到目录 {ANIMALS_DIR}")

    for json_file in sorted(ANIMALS_DIR.glob("*.json")):
        with json_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        animal_id = data.get("id") or json_file.stem
        result[animal_id] = data
    return result
