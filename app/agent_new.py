# app/agent_new.py
"""Refactored agent system with specialized agents and routing."""
import os
import time as _time
from smolagents import CodeAgent, LiteLLMModel
from smolagents.utils import AgentGenerationError

from .agents import (
    build_analytics_agent, build_forecasting_agent,
    build_comparison_agent, build_drilldown_agent
)
from .router import router

# NOTE: In production, this imports from .tools and .util modules
# For demo purposes, these are simplified stubs
# See demo/demo_agent.py for a working example with mock data

# ---- Build Model ----
def _build_model():
    """Initialize Groq LLM via LiteLLM."""
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("Set GROQ_API_KEY to use Groq with RevOps Buddy.")
    os.environ["LITELLM_PROVIDER"] = "groq"
    os.environ["LITELLM_API_KEY"] = groq_key
    return LiteLLMModel(model_id="groq/llama-3.3-70b-versatile", temperature=0.2)

try:
    model = _build_model()
except Exception as e:
    print(f"[agent] Error building model: {e}")
    print("[agent] Agents will be initialized when API key is available.")
    model = None

# ---- Shorten Tool Docstrings ----
def _shorten_doc(fn):
    """Reduce tool docstrings to save tokens."""
    doc = (fn.__doc__ or "").strip().splitlines()
    head = doc[0] if doc else ""
    fn.__doc__ = head[:180]
    return fn

# ---- Build Agents ----
def _build_general_agent(tools, max_steps):
    """Build general-purpose agent for complex queries."""
    return CodeAgent(
        tools=tools,
        model=model,
        add_base_tools=False,
        name="revops_buddy",
        instructions=(
            "You are a concise RevOps assistant. "
            "Default to the SALES/REVOPS meaning of the word 'pipeline' (not energy pipelines). "
            "Use ONLY the provided tools; do not answer from world knowledge when a tool returns no data. "
            "If a tool returns empty/None OR a dict containing the key 'error', respond with that message "
            "and suggest one relevant follow-up query. "
            "When you call final_answer, NEVER pass a bare number or dict; always compose a short, human sentence "
            "in natural language with currency formatting (e.g., 'Total pipeline in Iberia is €3.99M'). "
            "Prefer the most specific tool available. Keep answers short."
        ),
        max_steps=max_steps,
    )

# Agent registry (initialized with specialized agents)
AGENTS = {}

def initialize_agents(all_tools, voice_tools):
    """
    Initialize all agents with their respective tools.
    
    This is called after tools are loaded/defined.
    In production, tools are imported from app.tools module.
    """
    global AGENTS
    
    if not model:
        print("[agent] Model not initialized - skipping agent setup")
        return
    
    # Shorten docstrings to save tokens
    all_tools = [_shorten_doc(t) for t in all_tools]
    voice_tools = [_shorten_doc(t) for t in voice_tools]
    
    # Build specialized agents
    analytics_agent = build_analytics_agent(model, max_steps=1)
    forecasting_agent = build_forecasting_agent(model, max_steps=2)
    comparison_agent = build_comparison_agent(model, max_steps=2)
    drilldown_agent = build_drilldown_agent(model, max_steps=3)
    general_agent = _build_general_agent(all_tools, max_steps=2)
    voice_agent = _build_general_agent(voice_tools, max_steps=1)
    
    AGENTS = {
        'analytics': analytics_agent,
        'forecasting': forecasting_agent,
        'comparison': comparison_agent,
        'drilldown': drilldown_agent,
        'general': general_agent,
        'voice': voice_agent,
    }

# ---- Public API ----
def run(query: str, mode: str = "full", debug: bool = False) -> str:
    """
    Run a query through the appropriate agent.

    Args:
        query: User's natural language query
        mode: 'full' for intelligent routing, 'voice' for voice mode
        debug: If True, print routing information

    Returns:
        Natural language response
    """
    if not AGENTS:
        return "Agents not initialized. Call initialize_agents() first."
    
    # Route query to appropriate agent
    if mode == "voice":
        agent = AGENTS['voice']
        agent_type = 'voice'
    else:
        agent_type = router.route(query)
        agent = AGENTS.get(agent_type, AGENTS['general'])

    if debug:
        routing_info = router.explain_routing(query)
        print(f"[DEBUG] Routing: {routing_info}")
        print(f"[DEBUG] Using agent: {agent_type}")

    def _run_agent(q: str) -> str:
        return agent.run(q)

    try:
        out = _run_agent(query)
        return out

    except AgentGenerationError as e:
        msg = str(e).lower()
        if "rate limit" in msg or "tpm" in msg or "rate_limit" in msg:
            _time.sleep(2.0)
            try:
                return _run_agent(query[:300])
            except Exception:
                pass
        return ("I couldn't reach the reasoning model right now (likely rate limit). "
                "Please try again in ~20 seconds, or use a direct tool call.")
    except Exception as e:
        return f"Something went wrong while composing the answer: {e}"


def get_agent_stats() -> dict:
    """Get statistics about available agents and tools."""
    if not model:
        return {
            'agents': list(AGENTS.keys()) if AGENTS else [],
            'status': 'Not initialized (missing API key)',
        }

    return {
        'agents': list(AGENTS.keys()),
        'analytics_tools': len(AGENTS['analytics'].tools) if 'analytics' in AGENTS else 0,
        'forecasting_tools': len(AGENTS['forecasting'].tools) if 'forecasting' in AGENTS else 0,
        'comparison_tools': len(AGENTS['comparison'].tools) if 'comparison' in AGENTS else 0,
        'drilldown_tools': len(AGENTS['drilldown'].tools) if 'drilldown' in AGENTS else 0,
        'general_tools': len(AGENTS['general'].tools) if 'general' in AGENTS else 0,
        'voice_tools': len(AGENTS['voice'].tools) if 'voice' in AGENTS else 0,
    }
