import anyio
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from zakon_agent import registry
from zakon_agent.agents.sub_agent import shutdown_sub_agent
from zakon_agent.orchestrator import create_orchestrator


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

    for zakon_id in list(registry.agent_registry.keys()):
        print(f"Ukončuji agenta pro {zakon_id}...")
        await shutdown_sub_agent(zakon_id)


def run():
    anyio.run(main)


if __name__ == "__main__":
    run()
