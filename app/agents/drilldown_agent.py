# app/agents/drilldown_agent.py
"""Drill-down agent for detailed analysis."""
from smolagents import CodeAgent
from ..tools import (
    top_deals, deals_by_stage, pipeline_by_owner, owner_share_of_deals,
    top_owners_by_pipeline, stage_breakdown, pipeline_by_stage,
    open_deals, validate_pipeline_data
)

def build_drilldown_agent(model, max_steps: int = 3):
    """
    Build agent for detailed drill-down queries.

    Best for:
    - Top deals analysis
    - Specific stage inquiries
    - Owner deep-dives
    - Detailed breakdowns
    - Data quality checks
    """
    tools = [
        top_deals,
        deals_by_stage,
        pipeline_by_owner,
        owner_share_of_deals,
        top_owners_by_pipeline,
        stage_breakdown,
        pipeline_by_stage,
        open_deals,
        validate_pipeline_data,
    ]

    return CodeAgent(
        tools=tools,
        model=model,
        add_base_tools=False,
        name="drilldown_agent",
        instructions=(
            "You are a detail-oriented analyst for RevOps pipeline data. "
            "Provide comprehensive answers with specific deal names, owners, and stages. "
            "When listing top deals, include all relevant context (amount, stage, owner, close date). "
            "For owner queries, provide both pipeline amounts and deal counts. "
            "Use validate_pipeline_data when asked about data quality."
        ),
        max_steps=max_steps,
    )
