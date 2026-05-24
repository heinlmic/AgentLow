# Zákon Agent

Multi-agentní systém pro analýzu českých zákonů. Umožňuje stahovat zákony z [zakonyprolidi.cz](https://www.zakonyprolidi.cz), automaticky je analyzovat a následně se na ně dotazovat přirozeným jazykem.

## Jak to funguje

```
Uživatel (REPL)
    ↓
Orchestrátor (hlavní agent)
    ├── index.json — lokální databáze metadat zákonů
    ├── Temporary agent — jednorázová analýza textu zákona (JSON Schema výstup)
    └── Sub-agenti — perzistentní experti, jeden pro každý zákon
          → mají celý text zákona v kontextu
          → pamatují si historii dotazů v rámci session
```

Orchestrátor rozhoduje, které nástroje použít. Uživatel komunikuje přirozeným jazykem — orchestrátor sám zjistí, jestli zákon je v databázi, jestli je potřeba ho stáhnout, a přeposílá dotazy správnému sub-agentovi.

Metadata zákonů (název, summary, klíčové pojmy, seznam paragrafů, URL, výsledky analýzy) jsou uložena v `index.json`. Plné texty zákonů se ukládají do `data/zakony/` jako Markdown soubory a jsou vyloučeny z gitu — při dalším spuštění se automaticky re-stáhnou.

## Instalace

Vyžaduje Python 3.11+ a [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo>
cd AgentLow
uv sync
```

Nastav API klíč pro Anthropic:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Spuštění

```bash
uv run zakon
```

## Příklady použití

### Načtení a dotazování na zákon

```
=== Systém analýzy zákonů ===

Dotaz: Chci se zeptat na stavební zákon 183/2006

Asistent: Nalezl jsem zákon 183/2006 na zakonyprolidi.cz. Mám ho načíst?

Dotaz: Ano

Asistent: Zákon 183/2006 (Stavební zákon) připraven. Agent spuštěn.
          Co vás zajímá?

Dotaz: Jaké jsou podmínky pro vydání stavebního povolení?

Asistent: Podle § 110 stavebního zákona lze stavební povolení vydat, pokud:
          1. Projektová dokumentace splňuje požadavky...
          [Zaznamenal jsem odkaz na zákon 500/2004 (Správní řád). Chceš ho načíst?]
```

### Souběžná práce s více zákony

```
Dotaz: Načti mi i správní řád 500/2004

Asistent: Zákon 500/2004 (Správní řád) připraven. Agent spuštěn.

Dotaz: Jaká je lhůta pro odvolání podle správního řádu?

Asistent: Podle § 83 správního řádu je lhůta pro odvolání 15 dní...

Dotaz: A jak se tato lhůta vztahuje ke stavebnímu povolení?

Asistent: [dotaz přepošle oběma sub-agentům a zkříží odpovědi]
```

### Zásobník — odložené načtení

Pokud zákon zmíněný v odpovědi nechcete načítat hned, orchestrátor ho přidá do zásobníku a nabídne načtení později:

```
Dotaz: Ne, správní řád teď nepotřebuji

Asistent: Zákon 500/2004 přidán do zásobníku pro pozdější načtení.
          Zásobník: [500/2004]

Dotaz: Teď ho načti

Asistent: Zákon 500/2004 (Správní řád) připraven. Agent spuštěn.
```

### Ukončení

```
Dotaz: konec
```

Při ukončení každý sub-agent shrne probraná témata a zákon vydí ze seznamu aktivních agentů.

## Struktura projektu

```
AgentLow/
├── index.json                   # auto-generováno, není v repozitáři
├── data/
│   └── zakony/                  # texty zákonů (.md), nejsou v repozitáři
└── zakon_agent/
    ├── main.py                  # vstupní bod, REPL smyčka
    ├── orchestrator.py          # hlavní agent, MCP nástroje, hook
    ├── registry.py              # in-memory stav (aktivní agenti, zásobník)
    ├── store.py                 # čtení/zápis index.json
    ├── tools/
    │   ├── fetch_zakon.py       # stažení a konverze zákona do Markdown
    │   ├── structure_zakon.py   # regex parsing paragrafů a odkazů
    │   └── validate_url.py      # sestavení a validace URL na zakonyprolidi.cz
    └── agents/
        ├── temporary_agent.py   # jednorázová analýza zákona (structured output)
        └── sub_agent.py         # perzistentní expert na zákon
```

## Závislosti

| Balíček | Účel |
|---|---|
| `claude-agent-sdk` | SDK pro multi-agentní komunikaci s Claude |
| `httpx` | HTTP klient pro stahování zákonů |
| `beautifulsoup4` + `lxml` | parsování HTML a extrakce textu zákona |
| `anyio` | async runtime |
