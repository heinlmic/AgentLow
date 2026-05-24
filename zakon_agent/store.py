from pathlib import Path
import json

ROOT = Path(__file__).parent.parent
# Jeden soubor pro všechna metadata zákonů — klíč je "CISLO/ROK", např. "183/2006"
INDEX_PATH = ROOT / "index.json"


def load_index() -> dict:
    if not INDEX_PATH.exists():
        return {}
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def save_index(index: dict) -> None:
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def get_zakon(zakon_id: str) -> dict | None:
    return load_index().get(zakon_id)


def set_zakon(zakon_id: str, meta: dict) -> None:
    index = load_index()
    index[zakon_id] = meta
    save_index(index)
