# Plán zlepšení: Zákon Agent

Tento dokument navazuje na existující implementaci (popsanou v `description_plan.md` a `implementation_milestones.md`)
a popisuje konkrétní vylepšení identifikovaná po prvním testovacím běhu (`run_data.txt`).

---

## Přehled změn

| # | Soubor | Typ změny | Priorita |
|---|--------|-----------|----------|
| 1 | `main.py` | Hint "konec" v promptu | vysoká |
| 2 | `main.py` | Ochrana před druhým CTRL+C při shutdown | vysoká |
| 3 | `main.py` | Celková cena za session na konci | střední |
| 4 | `orchestrator.py` | Chybějící prázdný řádek (PEP 8) | nízká |
| 5 | `orchestrator.py` | Odstranění `_dummy` hacku u tools bez parametrů | nízká |
| 6 | `README.md` | Reálný příklad ze stavebního zákona | střední |

---

## Změna 1: Hint "konec" v promptu

**Problém:** Uživatel vidí pokyn pro ukončení jen v úvodním banneru. Pokud ho přehlédne, mačká CTRL+C.

**Soubor:** `zakon_agent/main.py`

**Aktuální stav (řádek 27):**
```python
dotaz = input("Dotaz: ").strip()
```

**Nový stav:**
```python
dotaz = input("Dotaz (nebo 'konec'): ").strip()
```

**Ověření:** Spustit `uv run zakon`, zkontrolovat že prompt zobrazuje nápovědu.

---

## Změna 2: Ochrana před druhým CTRL+C při shutdown

**Problém:** Pokud uživatel stiskne CTRL+C znovu během ukončování sub-agentů (řádky 38–40),
program skončí s `KeyboardInterrupt` traceback místo čistého ukončení.

**Soubor:** `zakon_agent/main.py`

**Aktuální stav (řádky 38–40):**
```python
for zakon_id in list(registry.agent_registry.keys()):
    print(f"Ukončuji agenta pro {zakon_id}...")
    await shutdown_sub_agent(zakon_id)
```

**Nový stav:**
```python
try:
    for zakon_id in list(registry.agent_registry.keys()):
        print(f"Ukončuji agenta pro {zakon_id}...")
        await shutdown_sub_agent(zakon_id)
except KeyboardInterrupt:
    print("\nVynucené ukončení — agenti nebyli řádně uzavřeni.")
```

**Ověření:** Spustit program, načíst zákon, napsat "konec" nebo CTRL+C, pak při tisku
"Ukončuji agenta..." stisknout CTRL+C znovu — program musí skončit bez tracebacku.

---

## Změna 3: Celková cena za session na konci

**Problém:** Cena se zobrazuje po každém dotazu, ale uživatel nevidí celkový součet za celou session.

**Soubor:** `zakon_agent/main.py`

**Implementace:** Přidat akumulátor `session_cost` před smyčkou, přičítat po každém dotazu,
vytisknout součet na konci.

**Aktuální stav:**
```python
async def main():
    print("=== Systém analýzy zákonů ===")
    print("Zadej dotaz nebo 'konec' pro ukončení.\n")

    async with create_orchestrator() as orchestrator:
        while True:
            ...
            async for message in orchestrator.receive_response():
                ...
                elif isinstance(message, ResultMessage):
                    if message.total_cost_usd and message.total_cost_usd > 0:
                        print(f"[${message.total_cost_usd:.4f}]")
            print()

    for zakon_id in list(registry.agent_registry.keys()):
        ...
```

**Nový stav:**
```python
async def main():
    print("=== Systém analýzy zákonů ===")
    print("Zadej dotaz nebo 'konec' pro ukončení.\n")

    session_cost = 0.0

    async with create_orchestrator() as orchestrator:
        while True:
            ...
            async for message in orchestrator.receive_response():
                ...
                elif isinstance(message, ResultMessage):
                    if message.total_cost_usd and message.total_cost_usd > 0:
                        session_cost += message.total_cost_usd
                        print(f"[${message.total_cost_usd:.4f}]")
            print()

    try:
        for zakon_id in list(registry.agent_registry.keys()):
            print(f"Ukončuji agenta pro {zakon_id}...")
            await shutdown_sub_agent(zakon_id)
    except KeyboardInterrupt:
        print("\nVynucené ukončení — agenti nebyli řádně uzavřeni.")

    if session_cost > 0:
        print(f"\nCelková cena session: ${session_cost:.4f}")
```

**Ověření:** Projít několik dotazů, zadat "konec", zkontrolovat že poslední řádek zobrazuje
součet odpovídající sumě dílčích cen.

---

## Změna 4: Chybějící prázdný řádek v orchestrator.py (PEP 8)

**Problém:** Mezi koncem funkce `_progress` a definicí konstant chybí prázdný řádek,
což porušuje PEP 8 (dvě prázdné řádky mezi top-level definicemi).

**Soubor:** `zakon_agent/orchestrator.py`

**Aktuální stav (řádky 49–52):**
```python
    finally:
        stop.set()
        await task
DATA_DIR = ROOT / "data" / "zakony"
```

**Nový stav:**
```python
    finally:
        stop.set()
        await task


DATA_DIR = ROOT / "data" / "zakony"
```

**Ověření:** `ruff check zakon_agent/orchestrator.py` nezahlásí E302.

---

## Změna 5: Odstranění `_dummy` hacku u tools bez parametrů

**Problém:** Tři nástroje bez parametrů (`list_active_agents`, `list_pending_zakony`,
`get_zakon_tree`) mají jako třetí argument `@tool` dekorátoru `{"_dummy": str}`,
což je workaround pro chybu nebo nejasnost v SDK. Pokud SDK nyní podporuje `{}`,
je lepší ho použít — kód je přesnější a Claude nebude generovat prázdný `_dummy` parametr.

**Soubor:** `zakon_agent/orchestrator.py`

**Dotčené řádky:**
```python
@tool("list_active_agents", "Vypiš zákon ID všech aktivních sub-agentů", {"_dummy": str})
@tool("list_pending_zakony", "Vypiš zásobník zákonů čekajících na načtení", {"_dummy": str})
@tool("get_zakon_tree", "Zobraz strom vztahů mezi načtenými zákony", {"_dummy": str})
```

**Nový stav:**
```python
@tool("list_active_agents", "Vypiš zákon ID všech aktivních sub-agentů", {})
@tool("list_pending_zakony", "Vypiš zásobník zákonů čekajících na načtení", {})
@tool("get_zakon_tree", "Zobraz strom vztahů mezi načtenými zákony", {})
```

**Riziko:** Pokud SDK vyžaduje neprázdný schema dict, volání těchto nástrojů selže.
V takovém případě vrátit zpět na `{"_dummy": str}`.

**Ověření:** Spustit `uv run zakon`, zadat dotaz "kdo je ted aktivni" — orchestrátor
musí zavolat `list_active_agents` a vrátit správný výsledek.

---

## Změna 6: README — reálný příklad ze stavebního zákona

**Problém:** Sekce "Příklady použití" v README obsahuje vymyšlené příklady,
které neukazují reálné chování (tabulkový výstup, zásobník, strom vztahů, ceny).

**Soubor:** `README.md`

**Co přidat:** Nový příklad "Porovnání dvou zákonů + zásobník" vycházející z `run_data.txt`:

```markdown
### Porovnání dvou zákonů + zásobník

```
Dotaz: jaka je minimalni vzdalenost objektu od hranice pozemku 283/2021
       abych nemusel resit stavebni povoleni?

Asistent: Zákon 283/2021 je v databázi. Spouštím sub-agenta.

          ## Minimální vzdálenost od hranice pozemku: **2 metry**

          | Typ stavby              | Plocha   | Výška | Vzdálenost od hranice |
          |-------------------------|----------|-------|-----------------------|
          | Stavba na pozemku RD    | max 40 m²| max 5m| min. 2 m              |
          | Skleník nebo bazén      | max 40 m²| max 5m| min. 2 m              |
          | Rekreační oblast        | max 55 m²| max 4m| min. 2 m              |

          > Zákon 183/2006 Sb. byl v odpovědi zmíněn. Přidávám do zásobníku.
          Zásobník: [183/2006]
          [$0.0968]

Dotaz: nacti 183/2006 a porovnej zasadni rozdil u staveb bez povoleni

Asistent: Načítám zákon 183/2006 ze zásobníku...

          ## Zásadní rozdíly: stavby bez povolení

          | | 183/2006 Sb. | 283/2021 Sb. |
          |---|---|---|
          | Systém | **3 stupně** | **2 stupně** |
          | Bez povolení | do 25 m² | do **40 m²** |
          | Ohlášení | ✅ existuje (§ 104) | ❌ zrušeno |

          Klíčová změna: ohlášení bylo zcela zrušeno. Co dříve stačilo ohlásit,
          dnes buď nevyžaduje nic, nebo vyžaduje plné povolení záměru.
          [$0.3571]

Dotaz: konec
Ukončuji agenta pro 283/2021...
Ukončuji agenta pro 183/2006...

Celková cena session: $0.4539
```
```

**Co upravit:** Stávající příklady ponechat (jsou didakticky dobré pro první pochopení),
reálný příklad přidat jako novou podsekci "Reálný příklad — stavební zákon".

**Ověření:** `cat README.md` zobrazí novou sekci s reálným příkladem.

---

## Pořadí implementace

Doporučené pořadí — každý bod je nezávislý, ale změny 1–3 jsou na sobě logicky navázané
(všechny jsou v `main.py`, implementuj je najednou):

1. **Změny 1 + 2 + 3** — `main.py` (prompt hint + CTRL+C + cena) — implementuj v jednom kroku
2. **Změna 4** — `orchestrator.py` (prázdný řádek) — triviální, 1 sekunda
3. **Změna 5** — `orchestrator.py` (_dummy hack) — nutný test po implementaci
4. **Změna 6** — `README.md` — nezávislé, lze kdykoli

---

## Testovací scénář po implementaci

```
1. spustit: uv run zakon
2. ověřit: prompt zobrazuje "(nebo 'konec')"
3. zadat: dotaz na zákon 283/2021
4. ověřit: cena se tiskne po odpovědi
5. zadat: "kdo je ted aktivni"  ← test změny 5 (_dummy)
6. zadat: "konec"
7. při tisku "Ukončuji..." stisknout CTRL+C
8. ověřit: žádný traceback, jen "Vynucené ukončení..."
9. ověřit: před ukončením vytiskne "Celková cena session: $X.XXXX"
```
