# Implementační milníky: Zákon Agent

Každý milník je samostatně implementovatelný a testovatelný.
Milník N **nesmí** začít dřív, než je dokončen milník N-1.

---

## Milník 1 — Projekt setup (základ, žádné závislosti)

**Co se implementuje:**
- `pyproject.toml` — závislosti + script entry point `zakon`
- Adresářová struktura: všechny složky + prázdné `__init__.py`
- `data/zakony/.gitkeep` — složka pro uložené texty zákonů

**Soubory:**
```
pyproject.toml
data/zakony/.gitkeep
zakon_agent/__init__.py
zakon_agent/tools/__init__.py
zakon_agent/agents/__init__.py
```

**Ověření:** `uv sync` proběhne bez chyby, `python -c "import zakon_agent"` nezahlásí chybu.

---

## Milník 2 — Datová vrstva (závisí na M1)

Čistý Python, žádná síť, žádný SDK. Lze testovat izolovaně.

**Co se implementuje:**

### 2a — `zakon_agent/store.py`
Funkce: `load_index()`, `save_index()`, `get_zakon()`, `set_zakon()`
- Čte/zapisuje `index.json` v kořeni projektu
- Pokud soubor neexistuje, vrátí prázdný dict

### 2b — `zakon_agent/registry.py`
Module-level singletony:
- `agent_registry: dict[str, ClaudeSDKClient]`
- `zakon_tree: dict[str, dict]`
- `pending_zakony: list[dict]`

**Ověření:**
```python
from zakon_agent.store import set_zakon, get_zakon
set_zakon("183/2006", {"nazev": "test"})
assert get_zakon("183/2006")["nazev"] == "test"
```

---

## Milník 3 — Utility nástroje (závisí na M1, síť nutná jen pro 3b/3c)

Každý soubor lze implementovat a testovat nezávisle.

### 3a — `zakon_agent/tools/structure_zakon.py`
Funkce: `structure_zakon(text: str) -> dict`
- Čistý Python, žádná síť
- Regex detekce paragrafů (`§ N název`) a odkazů (`zákon č. X/RRRR Sb.`)
- Vrací: `{"paragrafy": {...}, "odkazy": [...], "seznam_paragrafu": "..."}`

**Ověření:** Zavolej s ukázkovým textem, zkontroluj klíče ve výsledku.

### 3b — `zakon_agent/tools/validate_url.py`
Funkce: `build_url(zakon_id)`, `build_and_validate(zakon_id) -> dict`
- `build_url("183/2006")` → `"https://www.zakonyprolidi.cz/cs/2006-183"`
- `build_and_validate` pošle HEAD request, vrátí `{"zakon_id", "url", "valid"}`

**Ověření:** `asyncio.run(build_and_validate("183/2006"))` vrátí `valid: True`.

### 3c — `zakon_agent/tools/fetch_zakon.py`
Funkce: `fetch_zakon(url: str) -> str`
- httpx GET, BeautifulSoup lxml parser
- Odstraní nav/header/footer/script/style/aside
- Vrátí čistý text (prázdné řádky odstraněny)

**Ověření:** `asyncio.run(fetch_zakon(url))` vrátí neprázdný string bez HTML tagů.

---

## Milník 4 — Temporary agent (závisí na M1, M2, M3)

Jednorázové zpracování zákona přes `query()` s JSON Schema výstupem.

**Co se implementuje:** `zakon_agent/agents/temporary_agent.py`

Funkce: `process_zakon(zakon_id: str, zakon_text: str) -> dict`

- Používá top-level `query()` (ne `ClaudeSDKClient`) — session není potřeba
- `output_format={"type": "json_schema", "schema": ZAKON_SCHEMA}`
- Iteruje přes async generátor, zachytí `ResultMessage.structured_output`
- Vrátí dict: `{nazev, summary, klic_pojmy, system_prompt, seznam_paragrafu, nove_odkazy}`

**ZAKON_SCHEMA** — required pole:
```
nazev, summary, klic_pojmy, system_prompt, seznam_paragrafu, nove_odkazy
```

**Ověření:** Zavolej s textem zákon 183/2006 (staženo přes `fetch_zakon`), zkontroluj že výsledek obsahuje všechny required klíče a `nove_odkazy` je list.

---

## Milník 5 — Sub-agent (závisí na M1, M2, M4)

Perzistentní expert na konkrétní zákon s multi-turn pamětí.

**Co se implementuje:** `zakon_agent/agents/sub_agent.py`

### 5a — `spawn_sub_agent(zakon_id, meta) -> ClaudeSDKClient`
1. Vytvoří `ClaudeSDKClient` s `system_prompt` z meta
2. Zavolá `await client.__aenter__()` (ruční spawn mimo `async with`)
3. Pošle první turn s plným textem zákona
4. Spotřebuje potvrzovací odpověď (ignoruje obsah)
5. Vrátí klienta

### 5b — `shutdown_sub_agent(zakon_id) -> dict`
1. Načte klienta z `registry.agent_registry`
2. Pošle závěrečný dotaz: "Shrň co jsme probírali + vypiš zmíněné zákony jako JSON"
3. Zachytí text odpovědi, extrahuje JSON regexem
4. Zavolá `await client.__aexit__(None, None, None)`
5. Odstraní klienta z `registry.agent_registry`
6. Vrátí `{"discussed": [...], "nove_odkazy": [...]}`

**Ověření:** Spustit `spawn_sub_agent`, zeptat se přes `client.query()`, zavolat `shutdown_sub_agent` — zkontrolovat že registry je prázdný.

---

## Milník 6 — Orchestrátor (závisí na M1–M5)

Hlavní agent s MCP nástroji a hookem. Nejkomplexnější milník — implementuj postupně po skupinách nástrojů.

**Co se implementuje:** `zakon_agent/orchestrator.py`

### 6a — Helper + read-only nástroje
Implementuj jako první — čistě čtou stav, nic nespawnují:
```
_mcp_text(text) -> dict                   # helper
check_index_tool(zakon_id)                # get_zakon z store.py
list_active_agents_tool()                 # výpis registry.agent_registry
list_pending_tool()                       # výpis registry.pending_zakony
get_zakon_tree_tool()                     # výpis registry.zakon_tree
```

### 6b — URL a zásobník
```
validate_url_tool(zakon_id)               # build_and_validate
add_to_pending_tool(zakon_id, zminen_v)   # validace + přidání do pending_zakony
load_from_pending_tool(zakon_id)          # deleguje na spawn_zakon_agent_tool
```

### 6c — Spawn a ask (nejsložitější nástroje)
```
spawn_zakon_agent_tool(zakon_id, url)     # fetch → process → spawn → uloží do indexu
ask_zakon_agent_tool(zakon_id, dotaz)     # deleguje na sub-agent
```

### 6d — Hook
```python
inject_context_hook(input_data, tool_use_id, context) -> HookJSONOutput
```
Vloží do každého promptu: aktivní agenty + zásobník jako `additionalContext`.

### 6e — System prompt + `create_orchestrator()`
- Definuj `SYSTEM_PROMPT` (string z plánu — sekce 5.4)
- `create_orchestrator()` sestaví `create_sdk_mcp_server()` + `ClaudeAgentOptions` + hook
- Vrátí `ClaudeSDKClient`

**Ověření:** `create_orchestrator()` nezahlásí chybu při inicializaci.

---

## Milník 7 — REPL smyčka (závisí na M6)

**Co se implementuje:** `zakon_agent/main.py`

Funkce: `main()` (async), `run()` (sync entry point pro pyproject.toml)

REPL logika:
1. `print` uvítací banner
2. `async with create_orchestrator() as orchestrator:`
3. `input("Dotaz: ")` — přeruš na `konec`/`exit`/`quit`/KeyboardInterrupt/EOFError
4. `await orchestrator.query(dotaz)`
5. Iteruj `orchestrator.receive_response()`:
   - `AssistantMessage` → tiskni `TextBlock.text`
   - `ResultMessage` → tiskni cenu pokud `total_cost_usd > 0`
6. Po ukončení smyčky: pro každý `zakon_id` v `registry.agent_registry` zavolej `shutdown_sub_agent`

**Ověření:** `uv run zakon` spustí REPL bez chyby, odpovídá na jednoduchý dotaz.

---

## Milník 8 — Volitelná rozšíření (závisí na M7, lze vynechat)

### 8a — Vizualizace stromu zákonů
Volba: **Pyvis** (interaktivní HTML) nebo **Mermaid** (export pro dokumentaci)
- Implementuj jako samostatný skript nebo příkaz v REPL (`/strom`)
- Závisí na `registry.zakon_tree` a `store.load_index()`

### 8b — Automatický výběr modelu podle velikosti zákona
Funkce: `select_model(token_count: int) -> str`
- `> 100 000 tokenů` → Gemini 1.5 Pro přes LiteLLM
- jinak → `claude-sonnet-4-6`
- Token count uložit do `index.json` → příště načíst bez měření

---

## Souhrn závislostí

```
M1 (setup)
 └── M2 (store + registry)
      └── M3a (structure_zakon)     ← čistý Python, lze souběžně s M2
      └── M3b (validate_url)        ← síť
      └── M3c (fetch_zakon)         ← síť
           └── M4 (temporary_agent)
                └── M5 (sub_agent)
                     └── M6 (orchestrator)
                          └── M7 (main / REPL)
                               └── M8 (volitelné)
```

M3a, M3b, M3c jsou na sobě navzájem nezávislé — lze implementovat v libovolném pořadí.
M6 se dělí na 6a → 6b → 6c → 6d → 6e — implementuj v tomto pořadí.
