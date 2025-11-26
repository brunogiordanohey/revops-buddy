# app/agents/comparison_agent.py
"""Comparison agent for cross-region/owner analysis."""
from smolagents import CodeAgent
from ..tools import (
    compare_regions, pipeline_amount_by_regions, top_owners_by_pipeline,
    pipeline_by_opportunity_type, pipeline_by_customer_type,
    stage_breakdown, average_arr
)

def build_comparison_agent(model, max_steps: int = 2):
    """
    Build agent for comparison queries.

    Best for:
    - Region vs region comparisons
    - Owner performance comparisons
    - Opportunity type breakdowns
    - Customer type analysis
    """
    tools = [
        compare_regions,
        pipeline_amount_by_regions,
        top_owners_by_pipeline,
        pipeline_by_opportunity_type,
        pipeline_by_customer_type,
        stage_breakdown,
        average_arr,
    ]

    return CodeAgent(
        tools=tools,
        model=model,
        add_base_tools=False,
        name="comparison_agent",
        instructions=(
            "You are a comparison specialist for RevOps data. "
            "Excel at comparing regions, owners, and deal categories. "
            "When comparing, always show differences and percentages where relevant. "
            "Use compare_regions for multi-region analysis. "
            "Present comparisons clearly with proper formatting."
        ),
        max_steps=max_steps,
    )
