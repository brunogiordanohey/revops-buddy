# app/agents/forecasting_agent.py
"""Forecasting agent for time-based predictions."""
from smolagents import CodeAgent
from ..tools import (
    weighted_forecast, weighted_forecast_by_region, deals_closing_within,
    rolling_monthly_pipeline, pipeline_total, count_deals
)

def build_forecasting_agent(model, max_steps: int = 2):
    """
    Build agent for forecasting and time-based queries.

    Best for:
    - Weighted forecasts
    - Deals closing within timeframes
    - Monthly trends
    - Future pipeline predictions
    """
    tools = [
        weighted_forecast,
        weighted_forecast_by_region,
        deals_closing_within,
        rolling_monthly_pipeline,
        pipeline_total,
        count_deals,
    ]

    return CodeAgent(
        tools=tools,
        model=model,
        add_base_tools=False,
        name="forecasting_agent",
        instructions=(
            "You are a forecasting specialist for RevOps pipeline data. "
            "Focus on probability-weighted forecasts, time-based predictions, and trend analysis. "
            "When asked about forecasts, always use weighted_forecast unless specifically asked for raw pipeline. "
            "Explain timeframes clearly (this_month, next_quarter, etc.). "
            "Keep answers concise with proper currency formatting."
        ),
        max_steps=max_steps,
    )
