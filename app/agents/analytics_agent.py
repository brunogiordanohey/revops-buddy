# app/agents/analytics_agent.py
"""Quick analytics agent for simple metrics."""
from smolagents import CodeAgent
from ..tools import (
    pipeline_total, count_deals, pipeline_by_stage, stage_breakdown,
    pipeline_amount, pipeline_amount_by_regions, average_arr,
    pipeline_by_country, count_deals_by_country, average_deal_size
)

def build_analytics_agent(model, max_steps: int = 1):
    """
    Build agent for quick analytics queries.

    Best for:
    - Total pipeline questions
    - Deal counts
    - Simple breakdowns by stage/region/country
    - Average metrics
    """
    tools = [
        pipeline_total,
        count_deals,
        pipeline_by_stage,
        stage_breakdown,
        pipeline_amount,
        pipeline_amount_by_regions,
        average_arr,
        pipeline_by_country,
        count_deals_by_country,
        average_deal_size,
    ]

    return CodeAgent(
        tools=tools,
        model=model,
        add_base_tools=False,
        name="analytics_agent",
        instructions=(
            "You are a fast analytics assistant for RevOps data. "
            "Answer queries about pipeline totals, deal counts, and basic breakdowns. "
            "Use the most specific tool available. Keep answers concise and include currency formatting. "
            "If no data is found, say so and suggest a related query."
        ),
        max_steps=max_steps,
    )
