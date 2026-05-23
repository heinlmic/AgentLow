import json
import re
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock

from zakon_agent import registry

MODEL = "claude-sonnet-4-6"


async def spawn_sub_agent(zakon_id: str, meta: dict) -> ClaudeSDKClient:
    client = ClaudeSDKClient(options=ClaudeAgentOptions(
        system_prompt=meta["system_prompt"],
        model=MODEL,
    ))
    await client.__aenter__()

    zakon_text = Path(meta["zakon_text_path"]).read_text(encoding="utf-8")

    await client.query(
        f"Zde je plné znění zákona {zakon_id} Sb. pro referenci:\n\n{zakon_text}\n\n"
        "Potvrď, že jsi zákon přijal a jsi připraven odpovídat na dotazy."
    )
    async for _ in client.receive_response():
        pass

    return client


async def shutdown_sub_agent(zakon_id: str) -> dict:
    client = registry.agent_registry.get(zakon_id)
    if not client:
        return {"discussed": [], "nove_odkazy": []}

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
        match = re.search(r"\{.*\}", summary_text, re.DOTALL)
        result = json.loads(match.group()) if match else {"discussed": [], "nove_odkazy": []}
    except Exception:
        result = {"discussed": [], "nove_odkazy": []}

    await client.__aexit__(None, None, None)
    del registry.agent_registry[zakon_id]
    return result
