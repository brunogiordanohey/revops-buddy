# app/router.py
"""Intelligent query router for agent selection."""
import re

class QueryRouter:
    """Routes queries to the most appropriate specialized agent."""

    # Keywords for each agent type
    ANALYTICS_KEYWORDS = [
        "total", "how many", "count", "number of", "average", "avg",
        "what's the", "what is", "breakdown", "by stage", "by region",
        "by country", "sum", "all deals"
    ]

    FORECASTING_KEYWORDS = [
        "forecast", "predict", "weighted", "probability", "closing",
        "this month", "next month", "this quarter", "next quarter",
        "within", "days", "trend", "rolling", "monthly"
    ]

    COMPARISON_KEYWORDS = [
        "compare", "vs", "versus", "difference", "between",
        "which region", "which owner", "by owner", "top owners",
        "opportunity type", "customer type", "reseller", "partner"
    ]

    DRILLDOWN_KEYWORDS = [
        "top deals", "biggest", "largest", "show me", "list",
        "which deals", "deal names", "specific", "details",
        "validate", "quality", "check", "open deals", "in stage"
    ]

    def __init__(self):
        """Initialize the router."""
        pass

    def route(self, query: str) -> str:
        """
        Determine which agent should handle the query.

        Args:
            query: User's natural language query

        Returns:
            Agent type: 'analytics', 'forecasting', 'comparison', 'drilldown', or 'general'
        """
        q_lower = query.lower().strip()

        # Count matches for each category
        scores = {
            'analytics': self._count_matches(q_lower, self.ANALYTICS_KEYWORDS),
            'forecasting': self._count_matches(q_lower, self.FORECASTING_KEYWORDS),
            'comparison': self._count_matches(q_lower, self.COMPARISON_KEYWORDS),
            'drilldown': self._count_matches(q_lower, self.DRILLDOWN_KEYWORDS),
        }

        # Special cases that override keyword matching
        if re.search(r'\b(forecast|weighted)\b', q_lower):
            return 'forecasting'

        if re.search(r'\b(compare|vs|versus)\b', q_lower):
            return 'comparison'

        if re.search(r'\b(top \d+|biggest|largest|show me deals)\b', q_lower):
            return 'drilldown'

        if re.search(r'\b(validate|data quality|check data)\b', q_lower):
            return 'drilldown'

        # Get highest scoring agent
        max_score = max(scores.values())

        # If no strong match, use general agent
        if max_score == 0:
            return 'general'

        # Return agent with highest score
        for agent_type, score in scores.items():
            if score == max_score:
                return agent_type

        return 'general'

    def _count_matches(self, query: str, keywords: list) -> int:
        """Count how many keywords appear in the query."""
        count = 0
        for keyword in keywords:
            if keyword in query:
                count += 1
        return count

    def explain_routing(self, query: str) -> dict:
        """
        Explain why a query was routed to a specific agent (for debugging).

        Args:
            query: User's query

        Returns:
            Dict with routing explanation
        """
        q_lower = query.lower().strip()
        agent = self.route(query)

        return {
            'query': query,
            'routed_to': agent,
            'analytics_score': self._count_matches(q_lower, self.ANALYTICS_KEYWORDS),
            'forecasting_score': self._count_matches(q_lower, self.FORECASTING_KEYWORDS),
            'comparison_score': self._count_matches(q_lower, self.COMPARISON_KEYWORDS),
            'drilldown_score': self._count_matches(q_lower, self.DRILLDOWN_KEYWORDS),
        }


# Singleton instance
router = QueryRouter()
