import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookMatcher,
    TextBlock,
    UserPromptSubmitHookInput,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import HookJSONOutput

from zakon_agent import registry
from zakon_agent.agents.sub_agent import spawn_sub_agent
from zakon_agent.agents.temporary_agent import process_zakon
from zakon_agent.store import get_zakon, set_zakon
from zakon_agent.tools.fetch_zakon import ZakonContentError, fetch_zakon
from zakon_agent.tools.validate_url import build_and_validate

ROOT = Path(__file__).parent.parent


async def _spinner_task(label: str, stop_event: asyncio.Event) -> None:
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    i = 0
    while not stop_event.is_set():
        print(f"\r  {frames[i % len(frames)]} {label}", end="", flush=True)
        i += 1
        await asyncio.sleep(0.1)
    print(f"\r  ✓ {label}          ", flush=True)


@asynccontextmanager
async def _progress(label: str):
    stop = asyncio.Event()
    task = asyncio.create_task(_spinner_task(label, stop))
    try:
        yield
    finally:
        stop.set()
        await task


# Kam se ukládají stažené texty zákonů jako .md soubory
DATA_DIR = ROOT / "data" / "zakony"

# Model pro orchestrátora — změň sem pokud chceš jiný model (platí i pro sub-agenty v sub_agent.py)
MODEL = "claude-sonnet-4-6"

# Hlavní chování orchestrátora — tady změníš jak Claude rozhoduje o zákonech,
# v jakém pořadí volá nástroje a co smí/nesmí odpovídat z vlastní znalosti.
SYSTEM_PROMPT = """Jsi orchestrátor systému pro analýzu českých zákonů.

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
   a. URL platná → informuj uživatele "Načítám zákon X/RRRR..." → spawn_zakon_agent(zakon_id, url) → ask_zakon_agent(dotaz)
   b. URL neplatná → informuj uživatele, požádej o URL ručně

## Detekce odkazů v odpovědích sub-agentů
Pokud odpověď sub-agenta obsahuje "zákon č. X/RRRR Sb." nebo "vyhláška č. X/RRRR Sb.":
1. check_index pro každý detekovaný předpis
2. Pokud není v databázi ani v zásobníku → add_to_pending(zakon_id, zminen_v=aktualni_zakon_id)
3. Na konci odpovědi upozorni: "Zaznamenal jsem odkaz na [X/RRRR]. Chceš ho načíst?"

## Pravidla
- NIKDY neodpovídej na právní dotazy z vlastní znalosti. Vždy použij ask_zakon_agent.
- Zákon ID je vždy formát "CISLO/ROK" (např. "183/2006").
- Pokud zásobník není prázdný, vždy nabídni načtení na konci odpovědi."""


# Každý nástroj musí vracet tento formát — MCP protokol to vyžaduje
def _mcp_text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


# --- 6a: Read-only nástroje ---

@tool("check_index", "Zkontroluj jestli zákon existuje v lokální databázi", {"zakon_id": str})
async def check_index_tool(args: dict[str, Any]) -> dict[str, Any]:
    meta = get_zakon(args["zakon_id"])
    if meta:
        return _mcp_text(json.dumps(meta, ensure_ascii=False))
    return _mcp_text("Zákon není v databázi")


@tool("list_active_agents", "Vypiš zákon ID všech aktivních sub-agentů", {})
async def list_active_agents_tool(args: dict[str, Any]) -> dict[str, Any]:
    if not registry.agent_registry:
        return _mcp_text("Žádní aktivní agenti")
    return _mcp_text(", ".join(registry.agent_registry.keys()))


@tool("list_pending_zakony", "Vypiš zásobník zákonů čekajících na načtení", {})
async def list_pending_tool(args: dict[str, Any]) -> dict[str, Any]:
    if not registry.pending_zakony:
        return _mcp_text("Zásobník je prázdný")
    return _mcp_text(json.dumps(registry.pending_zakony, ensure_ascii=False))


@tool("get_zakon_tree", "Zobraz strom vztahů mezi načtenými zákony", {})
async def get_zakon_tree_tool(args: dict[str, Any]) -> dict[str, Any]:
    return _mcp_text(json.dumps(registry.zakon_tree, ensure_ascii=False, indent=2))


# --- 6b: URL a zásobník ---

@tool("validate_zakon_url", "Ověř dostupnost URL pro zákon na zakonyprolidi.cz", {"zakon_id": str})
async def validate_url_tool(args: dict[str, Any]) -> dict[str, Any]:
    result = await build_and_validate(args["zakon_id"])
    return _mcp_text(json.dumps(result, ensure_ascii=False))


@tool(
    "add_to_pending",
    "Přidej zákon do zásobníku čekajícího na potvrzení uživatelem",
    {"zakon_id": str, "zminen_v": str},
)
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


@tool(
    "load_from_pending",
    "Načti a spusť zákon ze zásobníku (vyžaduje souhlas uživatele předem)",
    {"zakon_id": str},
)
async def load_from_pending_tool(args: dict[str, Any]) -> dict[str, Any]:
    zakon_id = args["zakon_id"]
    entry = next((p for p in registry.pending_zakony if p["zakon_id"] == zakon_id), None)
    if not entry:
        return _mcp_text(f"Zákon {zakon_id} není v zásobníku")
    if not entry["valid"]:
        return _mcp_text(
            f"URL pro zákon {zakon_id} není platná. Zadej URL ručně pomocí spawn_zakon_agent."
        )
    result = await spawn_zakon_agent_tool({"zakon_id": zakon_id, "url": entry["url"]})
    registry.pending_zakony.remove(entry)
    return result


# --- 6c: Spawn a ask ---

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
    # Zákon může být v index.json ale .md soubor manuálně smazaný — pak stahujeme znovu
    text_missing = meta is not None and not Path(meta["zakon_text_path"]).exists()

    if not meta or text_missing:
        download_url = url or (meta["url"] if meta else "")
        if not download_url:
            return _mcp_text(f"Zákon {zakon_id} není v databázi a URL nebyla poskytnuta.")

        try:
            async with _progress(f"Stahuji zákon {zakon_id}"):
                zakon_text = await fetch_zakon(download_url)
        except ZakonContentError as e:
            return _mcp_text(f"Chyba obsahu: {e}")
        except Exception as e:
            return _mcp_text(f"Nepodařilo se stáhnout zákon {zakon_id}: {e}")

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        zakon_path = DATA_DIR / f"{zakon_id.replace('/', '_')}.md"
        zakon_path.write_text(zakon_text, encoding="utf-8")

        if not meta:
            # Nový zákon — temporary_agent vygeneruje system_prompt, summary atd.
            # Tohle je nejpomalejší krok (LLM analýza celého textu)
            async with _progress(f"Analyzuji zákon {zakon_id} (Claude AI — může trvat minutu)"):
                analysis = await process_zakon(zakon_id, zakon_text)
            # Struktura záznamu v index.json — pokud přidáváš pole, doplň i do ZAKON_SCHEMA v temporary_agent.py
            meta = {
                "nazev": analysis.get("nazev", zakon_id),
                "stazeno": date.today().isoformat(),
                "url": download_url,
                "zakon_text_path": str(zakon_path),
                "system_prompt": analysis["system_prompt"],
                "summary": analysis["summary"],
                "klic_pojmy": analysis["klic_pojmy"],
                "seznam_paragrafu": analysis["seznam_paragrafu"],
                "odkazy": analysis.get("nove_odkazy", []),
                "model": MODEL,
            }
        else:
            # text byl smazán, metadata v indexu jsou stále platná
            meta["zakon_text_path"] = str(zakon_path)

        set_zakon(zakon_id, meta)

    client = await spawn_sub_agent(zakon_id, meta)
    registry.agent_registry[zakon_id] = client
    registry.zakon_tree[zakon_id] = {
        "spawned_by": None,
        "children": meta.get("odkazy", []),
        "spawned_at": datetime.now(timezone.utc).isoformat(),
    }

    return _mcp_text(f"Zákon {zakon_id} ({meta['nazev']}) připraven. Agent spuštěn.")


@tool(
    "ask_zakon_agent",
    "Přepošli dotaz sub-agentovi pro konkrétní zákon",
    {"zakon_id": str, "dotaz": str},
)
async def ask_zakon_agent_tool(args: dict[str, Any]) -> dict[str, Any]:
    zakon_id = args["zakon_id"]
    client = registry.agent_registry.get(zakon_id)
    if not client:
        return _mcp_text(
            f"Agent pro zákon {zakon_id} neexistuje. Nejprve ho načti přes spawn_zakon_agent."
        )

    await client.query(args["dotaz"])

    odpoved = ""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    odpoved += block.text
    return _mcp_text(odpoved)


# --- 6d: Hook ---

# Hook se spustí před každým promptem — přidává orchestrátorovi kontext o běžících agentech.
# Pokud chceš přidat další automatický kontext (např. datum, uživatelské nastavení), doplň sem.
async def inject_context_hook(
    input_data: UserPromptSubmitHookInput,
    tool_use_id: str | None,
    context: HookContext,
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


# --- 6e: Sestavení orchestrátora ---

# Nový nástroj přidáš ve 3 krocích:
#   1. Definuj async funkci s @tool dekorátorem výše v tomto souboru
#   2. Přidej ji do seznamu tools= níže
#   3. Přidej "mcp__zakon_tools__<jmeno>" do allowed_tools=
def create_orchestrator() -> ClaudeSDKClient:
    zakon_tools = create_sdk_mcp_server(
        name="zakon_tools",
        version="1.0.0",
        tools=[
            check_index_tool,
            validate_url_tool,
            spawn_zakon_agent_tool,
            ask_zakon_agent_tool,
            list_active_agents_tool,
            add_to_pending_tool,
            list_pending_tool,
            load_from_pending_tool,
            get_zakon_tree_tool,
        ],
    )

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
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
