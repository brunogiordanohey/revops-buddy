#!/usr/bin/env python3
"""
Demo script for RevOps Buddy with mock data.

This demonstrates the multi-agent architecture without requiring real pipeline data.
Run: python demo/demo_agent.py
"""
import os
import sys
from typing import Dict, List
import pandas as pd
from smolagents import tool

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ---- Mock Data ----
MOCK_PIPELINE_DATA = pd.DataFrame([
    {
        "Deal_Name": "Acme Corp Enterprise",
        "Amount": 500000,
        "Stage": "Negotiation",
        "Region_norm": "iberia",
        "Country": "Spain",
        "Owner": "John Smith",
        "Probability": 75,
        "Close_Date": "2024-12-15",
        "ARR": 500000,
    },
    {
        "Deal_Name": "TechStart Expansion",
        "Amount": 250000,
        "Stage": "Proposal",
        "Region_norm": "iberia",
        "Country": "Portugal",
        "Owner": "Jane Doe",
        "Probability": 50,
        "Close_Date": "2024-12-20",
        "ARR": 250000,
    },
    {
        "Deal_Name": "Global Inc Renewal",
        "Amount": 800000,
        "Stage": "Negotiation",
        "Region_norm": "latam",
        "Country": "Mexico",
        "Owner": "Carlos Rodriguez",
        "Probability": 90,
        "Close_Date": "2025-01-10",
        "ARR": 800000,
    },
    {
        "Deal_Name": "StartupXYZ New",
        "Amount": 150000,
        "Stage": "Discovery",
        "Region_norm": "iberia",
        "Country": "Spain",
        "Owner": "John Smith",
        "Probability": 30,
        "Close_Date": "2025-02-01",
        "ARR": 150000,
    },
    {
        "Deal_Name": "Enterprise Solutions",
        "Amount": 1200000,
        "Stage": "Procurement/Legal",
        "Region_norm": "latam",
        "Country": "Brazil",
        "Owner": "Maria Silva",
        "Probability": 85,
        "Close_Date": "2024-12-30",
        "ARR": 1200000,
    },
])

# ---- Simplified Tools for Demo ----

def filter_df(region: str = None, stages: str = "") -> pd.DataFrame:
    """Filter mock data by region and/or stages."""
    df = MOCK_PIPELINE_DATA.copy()
    
    if region:
        region_norm = region.lower().strip()
        df = df[df["Region_norm"] == region_norm]
    
    if stages:
        stage_list = [s.strip().lower() for s in stages.split(",")]
        df = df[df["Stage"].str.lower().isin(stage_list)]
    
    return df


@tool
def pipeline_total() -> float:
    """Get total pipeline amount across all regions."""
    return float(MOCK_PIPELINE_DATA["Amount"].sum())


@tool
def pipeline_amount(region: str) -> float:
    """Get total pipeline for a specific region (iberia or latam)."""
    df = filter_df(region=region)
    return float(df["Amount"].sum())


@tool
def stage_breakdown(region: str = "") -> Dict[str, float]:
    """Get pipeline breakdown by stage, optionally filtered by region."""
    df = filter_df(region=region if region else None)
    return df.groupby("Stage")["Amount"].sum().to_dict()


@tool
def weighted_forecast(region: str = "") -> float:
    """Get probability-weighted forecast for a region or all regions."""
    df = filter_df(region=region if region else None)
    weighted = (df["Amount"] * df["Probability"] / 100).sum()
    return float(weighted)


@tool
def top_deals(n: int = 5, region: str = "") -> List[str]:
    """Get top N deals by amount, optionally filtered by region."""
    df = filter_df(region=region if region else None)
    df = df.nlargest(n, "Amount")
    
    results = []
    for _, row in df.iterrows():
        results.append(
            f"{row['Deal_Name']} — €{row['Amount']:,.0f} "
            f"({row['Stage']}, {row['Owner']})"
        )
    return results


@tool
def compare_regions() -> Dict[str, float]:
    """Compare total pipeline between regions."""
    return {
        "Iberia": float(filter_df(region="iberia")["Amount"].sum()),
        "Latam": float(filter_df(region="latam")["Amount"].sum()),
    }


@tool
def count_deals(region: str = "", stage: str = "") -> int:
    """Count number of deals, optionally filtered by region and/or stage."""
    df = filter_df(region=region if region else None, stages=stage)
    return len(df)


# ---- Demo Setup ----

# Define tools for each agent type
ANALYTICS_TOOLS = [pipeline_total, pipeline_amount, stage_breakdown, count_deals]
FORECASTING_TOOLS = [weighted_forecast, pipeline_total, count_deals]
COMPARISON_TOOLS = [compare_regions, pipeline_amount, stage_breakdown]
DRILLDOWN_TOOLS = [top_deals, count_deals, pipeline_amount]
ALL_TOOLS = [pipeline_total, pipeline_amount, stage_breakdown, weighted_forecast,
             top_deals, compare_regions, count_deals]
VOICE_TOOLS = [pipeline_total, pipeline_amount, stage_breakdown, count_deals]


def setup_demo():
    """Set up the demo environment."""
    # Import here to avoid circular imports
    from app import agent_new
    from app.agents import (
        build_analytics_agent, build_forecasting_agent,
        build_comparison_agent, build_drilldown_agent
    )
    
    # Check for API key
    if not os.getenv("GROQ_API_KEY"):
        print("⚠️  GROQ_API_KEY not set. Set it to run the demo:")
        print("   export GROQ_API_KEY='your-key-here'")
        sys.exit(1)
    
    # Initialize agents with demo tools
    print("🚀 Initializing RevOps Buddy agents...")
    agent_new.initialize_agents(ALL_TOOLS, VOICE_TOOLS)
    print("✅ Agents initialized!\n")
    
    return agent_new


def main():
    """Run the demo."""
    agent_new = setup_demo()
    
    # Demo queries
    queries = [
        "What's the total pipeline?",
        "What's the pipeline in Iberia?",
        "Compare Iberia vs Latam",
        "What's the weighted forecast?",
        "Show me the top 3 deals",
        "Break down pipeline by stage",
    ]
    
    print("=" * 60)
    print("RevOps Buddy Demo - Mock Pipeline Data")
    print("=" * 60)
    print()
    
    for query in queries:
        print(f"❓ {query}")
        try:
            answer = agent_new.run(query, debug=True)
            print(f"💡 {answer}")
        except Exception as e:
            print(f"❌ Error: {e}")
        print("-" * 60)
        print()
    
    # Interactive mode
    print("\n🎯 Interactive Mode (type 'quit' to exit)")
    print("-" * 60)
    
    while True:
        try:
            query = input("\n❓ Your query: ").strip()
            if not query or query.lower() in ['quit', 'exit', 'q']:
                break
            
            answer = agent_new.run(query, debug=False)
            print(f"💡 {answer}")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Error: {e}")
    
    print("\n👋 Thanks for trying RevOps Buddy!")


if __name__ == "__main__":
    main()
