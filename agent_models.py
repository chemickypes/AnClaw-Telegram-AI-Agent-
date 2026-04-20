from typing import Literal

from pydantic import BaseModel


class AgentSpec(BaseModel):
    name: str
    role: str
    instructions: str
    is_pure_llm: bool = False


class ArchitectPlan(BaseModel):
    goal: str
    intermediate_message: str
    team_name: str
    team_mode: Literal["coordinate", "route", "broadcast"]
    agents: list[AgentSpec]


_ARCHITECT_HINT = (
    "\n\nATTENZIONE: la tua risposta precedente non era un piano valido. "
    "Rispondi SOLO con un JSON che corrisponde esattamente allo schema ArchitectPlan. "
    "Campi obbligatori: goal (str), intermediate_message (str), team_name (str), "
    "team_mode (\"coordinate\"|\"route\"|\"broadcast\"), "
    "agents (lista di oggetti con name, role, instructions, is_pure_llm). "
    "Nessun testo fuori dal JSON."
)

_FALLBACK_PLAN = ArchitectPlan(
    goal="Rispondere alla richiesta dell'utente",
    intermediate_message="Elaboro la tua richiesta...",
    team_name="Fallback Team",
    team_mode="route",
    agents=[AgentSpec(
        name="SynthAgent",
        role="Sintetizzatore",
        instructions="Rispondi alla richiesta nel modo più utile possibile.",
        is_pure_llm=False,
    )],
)
