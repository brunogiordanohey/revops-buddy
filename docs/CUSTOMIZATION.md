# Customization & Extension Guide

This guide shows how to adapt RevOps Buddy for your specific use case.

## Adding Custom Tools

### 1. Basic Tool Structure

```python
from smolagents import tool
import pandas as pd

@tool
def my_custom_metric(region: str = "", threshold: float = 0.0) -> dict:
    """
    Calculate custom business metric with optional filters.
    
    Args:
        region: Optional region filter
        threshold: Minimum value to include
        
    Returns:
        Dict with metric results
    """
    # Load your data
    df = load_data()
    
    # Apply filters
    if region:
        df = df[df['Region'] == region]
    if threshold:
        df = df[df['Amount'] >= threshold]
    
    # Calculate metric
    result = {
        'total': float(df['Amount'].sum()),
        'count': len(df),
        'average': float(df['Amount'].mean())
    }
    
    return result
```

### 2. Register Tool with Agents

```python
from app.agent_new import initialize_agents

# Add to appropriate toolset
ANALYTICS_TOOLS = [
    pipeline_total,
    my_custom_metric,  # Your new tool
    # ... other tools
]

# Initialize
initialize_agents(all_tools=ALL_TOOLS, voice_tools=VOICE_TOOLS)
```

### 3. Tool Best Practices

**DO:**
- Keep tools focused (single responsibility)
- Use clear, descriptive names
- Include comprehensive docstrings
- Return simple types (str, float, dict, list)
- Handle errors gracefully

**DON'T:**
- Make tools dependent on each other
- Return complex objects
- Include business logic in tool descriptions
- Use global state

## Creating Custom Agents

### 1. Define Agent Builder

```python
# app/agents/custom_agent.py
from smolagents import CodeAgent
from ..tools import tool1, tool2, tool3

def build_custom_agent(model, max_steps: int = 2):
    """
    Build agent for custom use case.
    
    Best for:
    - Specific query pattern 1
    - Specific query pattern 2
    """
    tools = [tool1, tool2, tool3]
    
    return CodeAgent(
        tools=tools,
        model=model,
        add_base_tools=False,
        name="custom_agent",
        instructions=(
            "You are a specialist for [specific domain]. "
            "When asked about [X], always use [tool_name]. "
            "Keep responses concise and include proper formatting. "
            "If data is missing, explain why and suggest alternatives."
        ),
        max_steps=max_steps,
    )
```

### 2. Update Router

```python
# app/router.py
class QueryRouter:
    CUSTOM_KEYWORDS = [
        "keyword1", "keyword2", "specific pattern"
    ]
    
    def route(self, query: str) -> str:
        q_lower = query.lower().strip()
        
        # Add custom routing logic
        custom_score = self._count_matches(q_lower, self.CUSTOM_KEYWORDS)
        
        # High-confidence patterns
        if "specific trigger" in q_lower:
            return 'custom'
        
        # ... rest of routing logic
```

### 3. Register in Agent System

```python
# app/agent_new.py
from .agents import build_custom_agent

# Initialize
custom_agent = build_custom_agent(model, max_steps=2)

AGENTS = {
    'analytics': analytics_agent,
    # ... other agents
    'custom': custom_agent,  # Your new agent
}
```

## Custom Data Sources

### 1. Database Integration

```python
# app/util/loaders.py
import pandas as pd
from sqlalchemy import create_engine

def load_df() -> pd.DataFrame:
    """Load pipeline data from PostgreSQL."""
    engine = create_engine(os.getenv('DATABASE_URL'))
    
    query = """
    SELECT 
        opportunity_name as Deal_Name,
        amount as Amount,
        stage as Stage,
        region as Region_norm,
        close_date as Close_Date,
        probability as Probability
    FROM opportunities
    WHERE status = 'open'
    """
    
    df = pd.read_sql(query, engine)
    return df
```

### 2. API Integration

```python
import requests
import pandas as pd

def load_df() -> pd.DataFrame:
    """Load pipeline from Salesforce API."""
    api_key = os.getenv('SALESFORCE_API_KEY')
    endpoint = "https://your-instance.salesforce.com/api/..."
    
    response = requests.get(
        endpoint,
        headers={'Authorization': f'Bearer {api_key}'}
    )
    
    data = response.json()
    df = pd.DataFrame(data['records'])
    
    # Normalize columns
    df = df.rename(columns={
        'Name': 'Deal_Name',
        'TotalAmount': 'Amount',
        # ... map other fields
    })
    
    return df
```

### 3. CSV with Custom Processing

```python
def load_df() -> pd.DataFrame:
    """Load with custom normalization."""
    df = pd.read_csv('pipeline.csv')
    
    # Normalize regions
    df['Region_norm'] = df['Region'].str.lower().map({
        'spain': 'iberia',
        'portugal': 'iberia',
        'mexico': 'latam',
        'brazil': 'latam',
    })
    
    # Parse dates
    df['Close_Date'] = pd.to_datetime(df['Close_Date'])
    
    # Clean amounts
    df['Amount'] = pd.to_numeric(
        df['Amount'].str.replace('$', '').str.replace(',', ''),
        errors='coerce'
    )
    
    return df
```

## Voice Customization

### 1. Custom Wake Words

```python
# app/voice_loop.py
_WAKE_PATTERNS = [
    r"\bhey\s+assistant\b",
    r"\bcomputer\b",
    r"\byour-custom-wake-word\b",
]
```

### 2. Multi-Language Support

```python
# Spanish voice interface
WHISPER_LANGUAGE = "es"
VOICE_NAME = "Monica"  # Spanish voice

# Update wake patterns for Spanish
_WAKE_PATTERNS = [
    r"\basistente\b",
    r"\bbuddy\b",
]
```

### 3. Custom TTS

Replace macOS `say` with cross-platform solution:

```python
import pyttsx3

def speak(text: str):
    """Cross-platform text-to-speech."""
    engine = pyttsx3.init()
    engine.setProperty('rate', 200)
    engine.setProperty('voice', 'english')
    engine.say(text)
    engine.runAndWait()
```

## Advanced Routing

### 1. Intent Classification

```python
class QueryRouter:
    def route(self, query: str) -> str:
        # Use embeddings for semantic matching
        query_embedding = self._get_embedding(query)
        
        similarities = {
            'analytics': cosine_sim(query_embedding, analytics_centroid),
            'forecasting': cosine_sim(query_embedding, forecast_centroid),
            # ...
        }
        
        return max(similarities, key=similarities.get)
```

### 2. Context-Aware Routing

```python
class StatefulRouter:
    def __init__(self):
        self.conversation_history = []
    
    def route(self, query: str) -> str:
        # Consider previous queries
        if self.conversation_history:
            last_agent = self.conversation_history[-1]['agent']
            
            # If follow-up question, use same agent
            if query.startswith(("and", "also", "what about")):
                return last_agent
        
        # Standard routing
        agent = self._standard_route(query)
        self.conversation_history.append({
            'query': query,
            'agent': agent
        })
        
        return agent
```

## Custom Response Formatting

### 1. Add Post-Processing

```python
def run(query: str, format: str = "text") -> str:
    """Run query with custom formatting."""
    answer = agent.run(query)
    
    if format == "json":
        return json.dumps({"query": query, "answer": answer})
    elif format == "markdown":
        return f"**Q:** {query}\n\n**A:** {answer}"
    elif format == "slack":
        return {
            "text": answer,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": answer}}]
        }
    
    return answer
```

### 2. Streaming Responses

```python
def run_stream(query: str):
    """Stream response tokens as they're generated."""
    agent_type = router.route(query)
    agent = AGENTS[agent_type]
    
    for token in agent.run_streaming(query):
        yield token
```

## Performance Optimization

### 1. Caching Layer

```python
from functools import lru_cache
import hashlib

@lru_cache(maxsize=100)
def cached_run(query_hash: str):
    """Cache responses for repeated queries."""
    return run(query)

def run_cached(query: str):
    query_hash = hashlib.md5(query.encode()).hexdigest()
    return cached_run(query_hash)
```

### 2. Batch Processing

```python
def run_batch(queries: list[str]) -> list[dict]:
    """Process multiple queries efficiently."""
    results = []
    
    # Group by agent type
    grouped = {}
    for query in queries:
        agent_type = router.route(query)
        grouped.setdefault(agent_type, []).append(query)
    
    # Process each group
    for agent_type, agent_queries in grouped.items():
        agent = AGENTS[agent_type]
        for query in agent_queries:
            answer = agent.run(query)
            results.append({
                'query': query,
                'answer': answer,
                'agent': agent_type
            })
    
    return results
```

## Testing Custom Components

### 1. Tool Testing

```python
# tests/test_custom_tools.py
import pytest
from app.tools.custom import my_custom_metric

def test_custom_metric():
    result = my_custom_metric(region="iberia")
    assert 'total' in result
    assert result['total'] > 0
    assert result['count'] >= 0

def test_custom_metric_with_threshold():
    result = my_custom_metric(threshold=100000)
    assert result['total'] >= result['count'] * 100000
```

### 2. Agent Testing

```python
# tests/test_agents.py
def test_custom_agent_routing():
    query = "specific pattern that triggers custom agent"
    agent_type = router.route(query)
    assert agent_type == 'custom'

def test_custom_agent_response():
    query = "test query for custom agent"
    answer = run(query)
    assert answer
    assert "expected pattern" in answer.lower()
```

## Deployment Configurations

### 1. Docker Setup

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ ./app/
COPY demo/ ./demo/

# Set environment
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app.voice_loop"]
```

### 2. Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  revops-buddy:
    build: .
    environment:
      - GROQ_API_KEY=${GROQ_API_KEY}
      - DATABASE_URL=${DATABASE_URL}
    volumes:
      - ./data:/app/data
    ports:
      - "8000:8000"
```

## Integration Examples

### 1. Slack Bot

```python
from slack_bolt import App

app = App(token=os.getenv("SLACK_BOT_TOKEN"))

@app.message(".*")
def handle_message(message, say):
    query = message['text']
    answer = run(query)
    say(answer)
```

### 2. REST API

```python
from fastapi import FastAPI

app = FastAPI()

@app.post("/query")
async def query(request: dict):
    query = request['query']
    mode = request.get('mode', 'full')
    answer = run(query, mode=mode)
    return {"answer": answer}
```

### 3. Gradio Interface

```python
import gradio as gr

def gradio_query(query, mode):
    return run(query, mode=mode)

interface = gr.Interface(
    fn=gradio_query,
    inputs=[
        gr.Textbox(label="Query"),
        gr.Radio(["full", "voice"], label="Mode")
    ],
    outputs=gr.Textbox(label="Answer"),
    title="RevOps Buddy"
)

interface.launch()
```

---

*For more examples, see the `examples/` directory.*
