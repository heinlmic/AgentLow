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
