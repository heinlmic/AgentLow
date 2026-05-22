from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

# Aktivní sub-agenti (zakon_id → ClaudeSDKClient instance)
agent_registry: dict[str, "ClaudeSDKClient"] = {}

# Strom vztahů — kdo koho odkazuje
zakon_tree: dict[str, dict] = {}

# Zásobník — zákon čeká na potvrzení uživatelem
pending_zakony: list[dict] = []
