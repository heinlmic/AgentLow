import anyio
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from zakon_agent import registry
from zakon_agent.agents.sub_agent import shutdown_sub_agent
from zakon_agent.orchestrator import create_orchestrator


# Vstupní bod aplikace — spouští se přes `python -m zakon_agent` nebo příkazem definovaným v pyproject.toml
async def main():
    print("=== Systém analýzy zákonů ===")
    print("Zadej dotaz nebo 'konec' pro ukončení.\n")

    session_cost = 0.0

    async with create_orchestrator() as orchestrator:
        while True:
            try:
                dotaz = input("Dotaz (nebo 'konec'): ").strip()
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


def run():
    anyio.run(main)


if __name__ == "__main__":
    run()
