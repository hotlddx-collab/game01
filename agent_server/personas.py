"""加载 ../data/animals/*.json 形成 persona 字典 + 地点描述。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


# 项目根 = agent_server/.. = repo 根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANIMALS_DIR = PROJECT_ROOT / "data" / "animals"
LOCATION_DESC_FILE = PROJECT_ROOT / "data" / "world" / "location_descriptions.json"


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


_location_desc_cache: Dict[str, str] | None = None


def get_location_description(loc_id: str) -> str:
    """返回地点描述（注入对话 prompt 用）。找不到返回空串。"""
    global _location_desc_cache
    if _location_desc_cache is None:
        _location_desc_cache = {}
        if LOCATION_DESC_FILE.exists():
            with LOCATION_DESC_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                # 跳过 _comment 字段
                _location_desc_cache = {k: v for k, v in data.items()
                                       if not k.startswith("_")}
    return _location_desc_cache.get(loc_id, "")

