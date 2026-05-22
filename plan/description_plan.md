# Implementační plán: Multi-agent systém pro analýzu zákonů

## Přehled architektury

```
Uživatel
   ↓
Hlavní agent / orchestrátor (ClaudeSDKClient, persistentní session)
   ├── index.json (persistence metadata zákonů na disku)
   ├── agent_registry: dict (živí sub-agenti v paměti)
   ├── zakon_tree: dict (strom vztahů mezi zákony)
   ├── pending_zakony: list (zásobník zákonů čekajících na potvrzení)
   │
   ├── Temporary agent (query() s output_format JSON Schema)
   │     → stáhne + analyzuje zákon
   │     → vrátí structured_output: summary, system_prompt, klic_pojmy, ...
   │     → zanikne po jednom volání (query() nemá session)
   │
   └── Sub-agenti (ClaudeSDKClient, persistentní session po dobu běhu)
         → každý má plný text svého zákona v prvním turn
         → pamatují si historii dotazů (multi-turn paměť)
         → spawnovány přes __aenter__(), ukončovány přes __aexit__()
```

---

## Adresářová struktura projektu

```
AgentLow/
├── pyproject.toml               # závislosti: claude-agent-sdk, httpx, beautifulsoup4, anyio, lxml
├── index.json                   # auto-generováno, není v repozitáři
├── data/
│   └── zakony/                  # uložené plné texty zákonů (.txt)
│       ├── 183_2006.txt
│       └── 268_2009.txt
└── zakon_agent/                 # Python balíček (obsahuje __init__.py)
    ├── __init__.py
    ├── main.py                  # vstupní bod: anyio.run(main), REPL smyčka
    ├── orchestrator.py          # hlavní agent: @tool funkce, create_sdk_mcp_server, hooks
    ├── registry.py              # module-level singleton: agent_registry, zakon_tree, pending_zakony
    ├── store.py                 # index.json helpers: load_index, save_index, get_zakon, set_zakon
    ├── tools/                   # Python utility funkce (NEJSOU to SDK skills)
    │   ├── __init__.py
    │   ├── fetch_zakon.py       # httpx + BeautifulSoup → čistý text
    │   ├── structure_zakon.py   # parsování paragrafů, regex detekce odkazů
    │   └── validate_url.py      # build_url + httpx HEAD validace
    └── agents/
        ├── __init__.py
        ├── temporary_agent.py   # query() + output_format JSON Schema
        └── sub_agent.py         # spawn_sub_agent + shutdown_sub_agent
```

Spuštění: `python -m zakon_agent.main` (nebo `uv run zakon` přes pyproject.toml script)

---

## Fáze 1: Persistence a datové struktury

### 1.1 Schéma index.json

```json
{
  "183/2006": {
    "nazev": "Stavební zákon",
    "stazeno": "2026-05-19",
    "url": "https://www.zakonyprolidi.cz/cs/2006-183",
    "zakon_text_path": "data/zakony/183_2006.txt",
    "system_prompt": "Jsi expert na zákon 183/2006...",
    "summary": "Upravuje územní plánování a stavební řád...",
    "klic_pojmy": ["stavební povolení", "územní řízení"],
    "seznam_paragrafu": "§1 Předmět úpravy, §15 Územní řízení...",
    "odkazy": ["268/2009", "500/2004"],
    "model": "claude-sonnet-4-6"
  }
}
```

### 1.2 store.py — helpers pro index.json

```python
from pathlib import Path
import json

ROOT = Path(__file__).parent.parent
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
```

### 1.3 registry.py — in-memory singleton stav

```python
from claude_agent_sdk import ClaudeSDKClient

# Aktivní sub-agenti (zakon_id → ClaudeSDKClient instance)
agent_registry: dict[str, ClaudeSDKClient] = {}

# Strom vztahů — kdo koho odkazuje
zakon_tree: dict[str, dict] = {}
# Příklad:
# {
#   "183/2006": {
#     "spawned_by": None,           # root zákon
#     "children": ["268/2009"],     # zákony odkazované z tohoto
#     "spawned_at": "2026-05-19T10:00:00+00:00"
#   }
# }

# Zásobník — zákon byl detekován v odpovědi, čeká na potvrzení uživatelem
pending_zakony: list[dict] = []
# Příklad:
# [{"zakon_id": "268/2009", "url": "https://...", "zminen_v": "183/2006", "valid": True}]
```

---

## Fáze 2: Utility funkce (tools/)

### 2.1 tools/fetch_zakon.py

**Vstup:** URL zákona  
**Výstup:** čistý text zákona (odstraněna navigace, skripty, styly)

```python
import httpx
from bs4 import BeautifulSoup

async def fetch_zakon(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.body
    text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)
```

### 2.2 tools/structure_zakon.py

**Vstup:** čistý text  
**Výstup:** dict s paragrafy a odkazy

```python
import re

ODKAZ_PATTERN = re.compile(
    r'(?:zákon|vyhláška|nařízení)[a-z\s]*č\.\s*(\d+/\d{4})\s*Sb', re.IGNORECASE
)
PARAGRAF_PATTERN = re.compile(r'^§\s*(\d+\w*)\s*(.*?)$', re.MULTILINE)

def structure_zakon(text: str) -> dict:
    paragrafy = {}
    for match in PARAGRAF_PATTERN.finditer(text):
        cislo = f"§{match.group(1)}"
        nadpis = match.group(2).strip()
        paragrafy[cislo] = nadpis

    odkazy = sorted(set(ODKAZ_PATTERN.findall(text)))

    seznam_paragrafu = ", ".join(
        f"{k} {v}" if v else k for k, v in list(paragrafy.items())[:50]
    )

    return {
        "paragrafy": paragrafy,
        "odkazy": odkazy,
        "seznam_paragrafu": seznam_paragrafu,
    }
```

### 2.3 tools/validate_url.py

**URL šablona:** `https://www.zakonyprolidi.cz/cs/{rok}-{cislo}`

```python
import httpx

def build_url(zakon_id: str) -> str:
    cislo, rok = zakon_id.split("/")
    return f"https://www.zakonyprolidi.cz/cs/{rok}-{cislo}"

async def build_and_validate(zakon_id: str) -> dict:
    url = build_url(zakon_id)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
            response = await client.head(url)
            valid = response.status_code == 200
    except httpx.RequestError:
        valid = False
    return {"zakon_id": zakon_id, "url": url, "valid": valid}
```

---

## Fáze 3: Temporary agent

**Klíčové rozhodnutí:** Používáme top-level `query()` (NIKOLI `ClaudeSDKClient`), protože:
- Jde o jednorázové zpracování — session není potřeba
- `query()` s `output_format` vrátí `ResultMessage.structured_output` (dict přímo, bez `json.loads()`)
- Vlastní MCP nástroje nejsou potřeba → `query()` je dostačující

```python
# zakon_agent/agents/temporary_agent.py
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

ZAKON_SCHEMA = {
    "type": "object",
    "properties": {
        "nazev": {"type": "string"},
        "summary": {"type": "string"},
        "klic_pojmy": {"type": "array", "items": {"type": "string"}},
        "system_prompt": {"type": "string"},
        "seznam_paragrafu": {"type": "string"},
        "nove_odkazy": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["nazev", "summary", "klic_pojmy", "system_prompt", "seznam_paragrafu", "nove_odkazy"],
    "additionalProperties": False,
}

async def process_zakon(zakon_id: str, zakon_text: str) -> dict:
    options = ClaudeAgentOptions(
        max_turns=3,
        output_format={"type": "json_schema", "schema": ZAKON_SCHEMA},
    )

    # query() je async iterátor — NOT coroutine, iteruj přes něj
    async for message in query(
        prompt=f"""Analyzuj tento český zákon. Vrať:
- nazev: krátký název zákona
- summary: 3 věty o čem zákon je
- klic_pojmy: 5-10 klíčových právních pojmů
- system_prompt: system prompt pro AI experta na tento zákon (zahrň summary + seznam_paragrafu)
- seznam_paragrafu: prvních 50 paragrafů ve formátu "§1 Název, §2 Název..."
- nove_odkazy: seznam zákonů/vyhlášek odkazovaných v textu (formát ["268/2009", "500/2004"])

Zákon č. {zakon_id} Sb.:

{zakon_text[:100_000]}""",
        options=options,
    ):
        if isinstance(message, ResultMessage) and message.structured_output:
            return message.structured_output

    raise RuntimeError(f"Temporary agent nevrátil structured_output pro zákon {zakon_id}")
```

---

## Fáze 4: Sub-agent (expert)

**Klíčové rozhodnutí:** Používáme `ClaudeSDKClient` protože:
- Sub-agent musí pamatovat celou historii dotazů (multi-turn paměť)
- `query()` by ztratil kontext po každém dotazu
- Vlastní MCP nástroje nejsou potřeba → paměť dostačuje

**Vzor spouštění mimo `async with`:** Protože sub-agenti musí žít déle než jeden blok, voláme `__aenter__()` / `__aexit__()` ručně.

```python
# zakon_agent/agents/sub_agent.py
import json, re
from pathlib import Path
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage
from zakon_agent import registry

MODEL = "claude-sonnet-4-6"

async def spawn_sub_agent(zakon_id: str, meta: dict) -> ClaudeSDKClient:
    client = ClaudeSDKClient(options=ClaudeAgentOptions(
        system_prompt=meta["system_prompt"],
        model=MODEL,
    ))
    await client.__aenter__()

    zakon_text = Path(meta["zakon_text_path"]).read_text(encoding="utf-8")

    # První turn = nahrání plného textu zákona do kontextu
    await client.query(
        f"Zde je plné znění zákona {zakon_id} Sb. pro referenci:\n\n{zakon_text}\n\n"
        "Potvrď, že jsi zákon přijal a jsi připraven odpovídat na dotazy."
    )
    async for _ in client.receive_response():
        pass  # spotřebuj potvrzení, nepotřebujeme ho

    return client


async def shutdown_sub_agent(zakon_id: str) -> dict:
    client = registry.agent_registry.get(zakon_id)
    if not client:
        return {"discussed": [], "nove_odkazy": []}

    # Finální summary + zachycení odkazů zmíněných v průběhu hovoru
    await client.query(
        "Shrň stručně co jsme dnes probírali a vypiš všechny zákony nebo vyhlášky, "
        "které jsi v průběhu hovoru zmínil. Vrať JSON: "
        '{"discussed": ["témata..."], "nove_odkazy": ["268/2009"]}'
    )

    summary_text = ""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    summary_text += block.text

    try:
        match = re.search(r'\{.*\}', summary_text, re.DOTALL)
        result = json.loads(match.group()) if match else {"discussed": [], "nove_odkazy": []}
    except Exception:
        result = {"discussed": [], "nove_odkazy": []}

    await client.__aexit__(None, None, None)
    del registry.agent_registry[zakon_id]
    return result
```

---

## Fáze 5: Hlavní agent — orchestrátor

### 5.1 Napojení nástrojů na SDK — KRITICKÉ

Orchestrátor volá nástroje jako MCP tools. Každá funkce musí mít `@tool` dekorátor a vrátit `{"content": [{"type": "text", "text": "..."}]}`. Pak se zaregistrují přes `create_sdk_mcp_server()` a předají do `ClaudeAgentOptions`.

```python
# zakon_agent/orchestrator.py
from typing import Any
from claude_agent_sdk import (
    ClaudeSDKClient, ClaudeAgentOptions,
    create_sdk_mcp_server, tool,
    HookMatcher, AssistantMessage, TextBlock, ResultMessage,
)
from claude_agent_sdk.types import HookInput, HookContext, HookJSONOutput
from zakon_agent import registry
from zakon_agent.store import get_zakon, set_zakon
from zakon_agent.tools.fetch_zakon import fetch_zakon
from zakon_agent.tools.validate_url import build_and_validate
from zakon_agent.agents.temporary_agent import process_zakon
from zakon_agent.agents.sub_agent import spawn_sub_agent, shutdown_sub_agent
from pathlib import Path
from datetime import date, datetime, timezone
import json

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "zakony"
MODEL = "claude-sonnet-4-6"

def _mcp_text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}
```

### 5.2 @tool funkce

```python
@tool("check_index", "Zkontroluj jestli zákon existuje v lokální databázi", {"zakon_id": str})
async def check_index_tool(args: dict[str, Any]) -> dict[str, Any]:
    meta = get_zakon(args["zakon_id"])
    if meta:
        return _mcp_text(json.dumps(meta, ensure_ascii=False))
    return _mcp_text("Zákon není v databázi")


@tool("validate_zakon_url", "Ověř dostupnost URL pro zákon na zakonyprolidi.cz", {"zakon_id": str})
async def validate_url_tool(args: dict[str, Any]) -> dict[str, Any]:
    result = await build_and_validate(args["zakon_id"])
    return _mcp_text(json.dumps(result, ensure_ascii=False))


@tool(
    "spawn_zakon_agent",
    "Načti zákon a spusť sub-agenta. Pokud zákon je v databázi, url může být prázdný string.",
    {"zakon_id": str, "url": str},
)
async def spawn_zakon_agent_tool(args: dict[str, Any]) -> dict[str, Any]:
    zakon_id = args["zakon_id"]
    url = args.get("url", "")

    if zakon_id in registry.agent_registry:
        return _mcp_text(f"Agent pro {zakon_id} již běží.")

    meta = get_zakon(zakon_id)

    if not meta:
        if not url:
            return _mcp_text(f"Zákon {zakon_id} není v databázi a URL nebyla poskytnuta.")

        zakon_text = await fetch_zakon(url)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        zakon_path = DATA_DIR / f"{zakon_id.replace('/', '_')}.txt"
        zakon_path.write_text(zakon_text, encoding="utf-8")

        analysis = await process_zakon(zakon_id, zakon_text)

        meta = {
            "nazev": analysis.get("nazev", zakon_id),
            "stazeno": date.today().isoformat(),
            "url": url,
            "zakon_text_path": str(zakon_path),
            "system_prompt": analysis["system_prompt"],
            "summary": analysis["summary"],
            "klic_pojmy": analysis["klic_pojmy"],
            "seznam_paragrafu": analysis["seznam_paragrafu"],
            "odkazy": analysis.get("nove_odkazy", []),
            "model": MODEL,
        }
        set_zakon(zakon_id, meta)

    client = await spawn_sub_agent(zakon_id, meta)
    registry.agent_registry[zakon_id] = client
    registry.zakon_tree[zakon_id] = {
        "spawned_by": None,
        "children": meta.get("odkazy", []),
        "spawned_at": datetime.now(timezone.utc).isoformat(),
    }

    return _mcp_text(f"Zákon {zakon_id} ({meta['nazev']}) připraven. Agent spuštěn.")


@tool("ask_zakon_agent", "Přepošli dotaz sub-agentovi pro konkrétní zákon", {"zakon_id": str, "dotaz": str})
async def ask_zakon_agent_tool(args: dict[str, Any]) -> dict[str, Any]:
    zakon_id = args["zakon_id"]
    client = registry.agent_registry.get(zakon_id)
    if not client:
        return _mcp_text(f"Agent pro zákon {zakon_id} neexistuje. Nejprve ho načti přes spawn_zakon_agent.")

    await client.query(args["dotaz"])

    odpoved = ""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    odpoved += block.text
    return _mcp_text(odpoved)


@tool("list_active_agents", "Vypiš zákon ID všech aktivních sub-agentů", {})
async def list_active_agents_tool(args: dict[str, Any]) -> dict[str, Any]:
    if not registry.agent_registry:
        return _mcp_text("Žádní aktivní agenti")
    return _mcp_text(", ".join(registry.agent_registry.keys()))


@tool("add_to_pending", "Přidej zákon do zásobníku čekajícího na potvrzení uživatelem", {"zakon_id": str, "zminen_v": str})
async def add_to_pending_tool(args: dict[str, Any]) -> dict[str, Any]:
    zakon_id = args["zakon_id"]
    if any(p["zakon_id"] == zakon_id for p in registry.pending_zakony):
        return _mcp_text(f"Zákon {zakon_id} je již v zásobníku")
    result = await build_and_validate(zakon_id)
    registry.pending_zakony.append({
        "zakon_id": zakon_id,
        "url": result["url"],
        "zminen_v": args.get("zminen_v", ""),
        "valid": result["valid"],
    })
    status = "URL nalezena" if result["valid"] else "URL nenalezena — bude třeba zadat ručně"
    return _mcp_text(f"Zákon {zakon_id} přidán do zásobníku. {status}")


@tool("list_pending_zakony", "Vypiš zásobník zákonů čekajících na načtení", {})
async def list_pending_tool(args: dict[str, Any]) -> dict[str, Any]:
    if not registry.pending_zakony:
        return _mcp_text("Zásobník je prázdný")
    return _mcp_text(json.dumps(registry.pending_zakony, ensure_ascii=False))


@tool("load_from_pending", "Načti a spusť zákon ze zásobníku (vyžaduje souhlas uživatele předem)", {"zakon_id": str})
async def load_from_pending_tool(args: dict[str, Any]) -> dict[str, Any]:
    zakon_id = args["zakon_id"]
    entry = next((p for p in registry.pending_zakony if p["zakon_id"] == zakon_id), None)
    if not entry:
        return _mcp_text(f"Zákon {zakon_id} není v zásobníku")
    if not entry["valid"]:
        return _mcp_text(f"URL pro zákon {zakon_id} není platná. Zadej URL ručně pomocí spawn_zakon_agent.")
    result = await spawn_zakon_agent_tool({"zakon_id": zakon_id, "url": entry["url"]})
    registry.pending_zakony.remove(entry)
    return result


@tool("get_zakon_tree", "Zobraz strom vztahů mezi načtenými zákony", {})
async def get_zakon_tree_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _mcp_text(json.dumps(registry.zakon_tree, ensure_ascii=False, indent=2))
```

### 5.3 Hook — inject kontextu při každém promptu

```python
# UserPromptSubmit hook: vloží do každého promptu seznam aktivních agentů + zásobník
async def inject_context_hook(
    input_data: HookInput, tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    parts = []
    if registry.agent_registry:
        parts.append(f"Aktivní sub-agenti: {', '.join(registry.agent_registry.keys())}")
    if registry.pending_zakony:
        ids = [p["zakon_id"] for p in registry.pending_zakony]
        parts.append(f"Zásobník (čeká na potvrzení): {', '.join(ids)}")

    if parts:
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": " | ".join(parts),
            }
        }
    return {}
```

### 5.4 System prompt orchestrátora

```
Jsi orchestrátor systému pro analýzu českých zákonů.

## Nástroje
- check_index(zakon_id): je zákon v lokální databázi?
- validate_zakon_url(zakon_id): ověř URL na zakonyprolidi.cz
- spawn_zakon_agent(zakon_id, url): načti zákon a spusť sub-agenta
  → pokud zákon je v databázi: url=""
  → pokud zákon je nový: url=validovaná URL
- ask_zakon_agent(zakon_id, dotaz): přepošli dotaz sub-agentovi
- list_active_agents(): kdo běží
- add_to_pending(zakon_id, zminen_v): zákon do zásobníku
- list_pending_zakony(): zásobník
- load_from_pending(zakon_id): načti ze zásobníku
- get_zakon_tree(): strom vztahů

## Postup pro zákon zmíněný uživatelem (formát X/RRRR)
1. check_index(zakon_id)
2. JE v databázi → spawn_zakon_agent(zakon_id, url="") → ask_zakon_agent(...)
3. NENÍ v databázi → validate_zakon_url(zakon_id)
   a. URL platná → zeptej se uživatele: "Nalezl jsem zákon X/RRRR. Mám ho načíst?"
      - Uživatel ANO → spawn_zakon_agent(zakon_id, url)
      - Uživatel NE / SPÄTER → add_to_pending(zakon_id, zminen_v="")
   b. URL neplatná → informuj uživatele, požádej o URL ručně

## Detekce odkazů v odpovědích sub-agentů
Pokud odpověď sub-agenta obsahuje "zákon č. X/RRRR Sb." nebo "vyhláška č. X/RRRR Sb.":
1. check_index pro každý detekovaný předpis
2. Pokud není v databázi ani v zásobníku → add_to_pending(zakon_id, zminen_v=aktualni_zakon_id)
3. Na konci odpovědi upozorni: "Zaznamenal jsem odkaz na [X/RRRR]. Chceš ho načíst?"

## Pravidla
- NIKDY neodpovídej na právní dotazy z vlastní znalosti. Vždy použij ask_zakon_agent.
- Zákon ID je vždy formát "CISLO/ROK" (např. "183/2006").
- Pokud zásobník není prázdný, vždy nabídni načtení na konci odpovědi.
```

### 5.5 Sestavení orchestrátora

```python
def create_orchestrator() -> ClaudeSDKClient:
    zakon_tools = create_sdk_mcp_server(
        name="zakon_tools",
        version="1.0.0",
        tools=[
            check_index_tool, validate_url_tool, spawn_zakon_agent_tool,
            ask_zakon_agent_tool, list_active_agents_tool, add_to_pending_tool,
            list_pending_tool, load_from_pending_tool, get_zakon_tree_tool,
        ],
    )

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,  # string definovaný výše
        model=MODEL,
        mcp_servers={"zakon_tools": zakon_tools},
        allowed_tools=[
            "mcp__zakon_tools__check_index",
            "mcp__zakon_tools__validate_zakon_url",
            "mcp__zakon_tools__spawn_zakon_agent",
            "mcp__zakon_tools__ask_zakon_agent",
            "mcp__zakon_tools__list_active_agents",
            "mcp__zakon_tools__add_to_pending",
            "mcp__zakon_tools__list_pending_zakony",
            "mcp__zakon_tools__load_from_pending",
            "mcp__zakon_tools__get_zakon_tree",
        ],
        hooks={
            "UserPromptSubmit": [
                HookMatcher(matcher=None, hooks=[inject_context_hook]),
            ],
        },
    )

    return ClaudeSDKClient(options=options)
```

---

## Fáze 6: main.py — REPL smyčka

```python
# zakon_agent/main.py
import anyio
from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
from zakon_agent.orchestrator import create_orchestrator
from zakon_agent import registry
from zakon_agent.agents.sub_agent import shutdown_sub_agent

async def main():
    print("=== Systém analýzy zákonů ===")
    print("Zadej dotaz nebo 'konec' pro ukončení.\n")

    async with create_orchestrator() as orchestrator:
        while True:
            try:
                dotaz = input("Dotaz: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nUkončuji...")
                break

            if not dotaz:
                continue
            if dotaz.lower() in ("konec", "exit", "quit"):
                break

            await orchestrator.query(dotaz)

            async for message in orchestrator.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            print(f"\nAsistent: {block.text}")
                elif isinstance(message, ResultMessage):
                    if message.total_cost_usd and message.total_cost_usd > 0:
                        print(f"[${message.total_cost_usd:.4f}]")
            print()

    # Při ukončení uložit summaries aktivních agentů
    for zakon_id in list(registry.agent_registry.keys()):
        print(f"Ukončuji agenta pro {zakon_id}...")
        await shutdown_sub_agent(zakon_id)

def run():
    anyio.run(main)

if __name__ == "__main__":
    run()
```

---

## Fáze 7: pyproject.toml

```toml
[project]
name = "zakon-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "claude-agent-sdk",
    "httpx",
    "beautifulsoup4",
    "lxml",
    "anyio",
]

[project.scripts]
zakon = "zakon_agent.main:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## Fáze 8: Vizualizace stromu zákonů (volitelné)

Spustí se na vyžádání nebo při ukončení session.

### Varianta A: Pyvis (interaktivní HTML)

```python
from pyvis.network import Network
from zakon_agent import registry
from zakon_agent.store import load_index

def visualize_tree():
    index = load_index()
    net = Network(directed=True, height="600px")
    for zakon_id, meta in registry.zakon_tree.items():
        nazev = index.get(zakon_id, {}).get("nazev", zakon_id)
        net.add_node(zakon_id, label=f"{zakon_id}\n{nazev}")
        for child in meta.get("children", []):
            net.add_edge(zakon_id, child)
    net.show("zakon_tree.html")
```

### Varianta B: Mermaid (export do MD/dokumentace)

```python
def export_mermaid() -> str:
    lines = ["graph TD"]
    for zakon_id, meta in registry.zakon_tree.items():
        for child in meta.get("children", []):
            src = zakon_id.replace("/", "_")
            dst = child.replace("/", "_")
            lines.append(f"  {src} --> {dst}")
    return "\n".join(lines)
```

---

## Fáze 9: Model selection (volitelné rozšíření)

```python
def select_model(token_count: int) -> str:
    if token_count > 100_000:
        return "gemini/gemini-1.5-pro"   # přes LiteLLM
    return "claude-sonnet-4-6"
```

Token count se uloží do `index.json` → příště se model načte z indexu bez měření.

---

## Pořadí implementace

| Krok | Soubor | Co implementovat |
|------|--------|-----------------|
| 1 | `zakon_agent/__init__.py` | prázdný soubor (Python balíček) |
| 2 | `zakon_agent/registry.py` | module-level singletony (agent_registry, zakon_tree, pending_zakony) |
| 3 | `zakon_agent/store.py` | index.json read/write helpers |
| 4 | `zakon_agent/tools/fetch_zakon.py` | httpx + BeautifulSoup |
| 5 | `zakon_agent/tools/structure_zakon.py` | regex parsing paragrafů a odkazů |
| 6 | `zakon_agent/tools/validate_url.py` | build_url + HEAD validace |
| 7 | `zakon_agent/agents/temporary_agent.py` | query() + output_format JSON Schema |
| 8 | `zakon_agent/agents/sub_agent.py` | spawn_sub_agent + shutdown_sub_agent |
| 9 | `zakon_agent/orchestrator.py` | @tool funkce + hook + create_orchestrator() |
| 10 | `zakon_agent/main.py` | REPL smyčka + cleanup při ukončení |
| 11 | `pyproject.toml` | závislosti + script entry point |
| 12 | `data/zakony/` | prázdná složka (.gitkeep) |

---

## Otevřené otázky k rozhodnutí

- **Zdroj zákonů:** Plán počítá s `zakonyprolidi.cz` — pokud HTML struktura neodpovídá, fetch_zakon.py bude potřeba upravit pro konkrétní web.
- **Detekce novel:** Hash obsahu nebo datum v hlavičce stránky → není v první verzi, implementovat v kroku 2.
- **Context window limit:** SDK podporuje `resume=session_id` (viz `5_agent_with_memory.py`, Example 3) — při příliš dlouhé session lze session ID uložit do index.json a po restartu navázat.
- **Souběžné session:** Aktuální design je single-user. `agent_registry` a `pending_zakony` jsou module-level — pro více uživatelů by bylo potřeba je obalit do třídy nebo použít jiný stav.
- **Prázdný parametr `{}` u @tool:** Toolky bez parametrů (list_active_agents, list_pending_zakony, get_zakon_tree) používají `{}` jako třetí argument dekorátoru. Pokud SDK tuto variantu nepodporuje, použij `{"_dummy": str}` s ignorovanou hodnotou.
