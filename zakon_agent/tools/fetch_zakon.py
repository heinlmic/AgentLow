import re

import httpx
from bs4 import BeautifulSoup, Tag

# Přidá mezeru mezi značku odstavce a text: "(1)text" → "(1) text"
_MARKER_RE = re.compile(r'^(\(\d+\)|[a-záčšžýíéúůďťň]\w*\)|\d+\.)(.+)$')


def _add_space(text: str) -> str:
    text = text.strip()
    m = _MARKER_RE.match(text)
    return f"{m.group(1)} {m.group(2).strip()}" if m else text


def _frags_to_markdown(frags: Tag) -> str:
    parts: list[str] = []
    last_header_idx: int | None = None

    for el in frags.children:
        if not isinstance(el, Tag):
            continue

        cls = set(el.get("class", []))
        text = el.get_text(strip=True)

        # Přeskočit prázdné, oddělovače, poznámky pod čarou
        if not text or "AT" in cls or "PPC0" in cls or cls == {"L0"}:
            continue

        if "CAST" in cls:
            parts.append(f"\n## {text}")
            last_header_idx = len(parts) - 1
        elif "HLAVA" in cls:
            parts.append(f"\n### {text}")
            last_header_idx = len(parts) - 1
        elif "DIL" in cls:
            parts.append(f"\n#### {text}")
            last_header_idx = len(parts) - 1
        elif "ODDIL" in cls:
            parts.append(f"\n##### {text}")
            last_header_idx = len(parts) - 1
        elif "PARA" in cls:
            parts.append(f"\n### {text}")
            last_header_idx = len(parts) - 1
        elif "NADPIS" in cls or "TEMP" in cls:
            # Nadpis se přilepí k předchozímu strukturnímu prvku
            if last_header_idx is not None:
                parts[last_header_idx] += f" — {text}"
                last_header_idx = None
            else:
                parts.append(f"\n#### {text}")
        else:
            last_header_idx = None
            parts.append(_add_space(text))

    raw = "\n".join(parts).strip()

    # Odstraň víceřádkové prázdné řádky
    return re.sub(r"\n{3,}", "\n\n", raw)


class ZakonContentError(Exception):
    pass


async def fetch_zakon(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    # Odstraň navigaci webu
    for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()

    frags = soup.find(class_="Frags")
    if frags:
        text = _frags_to_markdown(frags)
        if len(text) < 200 or "§" not in text:
            raise ZakonContentError(
                f"Stránka {url} neobsahuje text zákona (možná přihlášení nebo prázdná stránka)."
            )
        return text

    # Fallback pro jiné weby — plain text bez struktury
    main = soup.find("main") or soup.find("article") or soup.body
    text = main.get_text(separator="\n", strip=True) if main else ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result = "\n".join(lines)
    if len(result) < 200:
        raise ZakonContentError(
            f"Stránka {url} neobsahuje použitelný obsah (možná přihlášení nebo prázdná stránka)."
        )
    return result
