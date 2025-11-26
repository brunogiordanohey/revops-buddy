# RevOps Buddy Architecture

## System Overview

RevOps Buddy is a multi-agent AI system designed for natural language querying of sales pipeline data. It combines intelligent query routing, specialized agents, and voice interface capabilities.

## Core Components

### 1. Query Router (`app/router.py`)

The router analyzes incoming queries and routes them to the most appropriate specialized agent.

**Routing Logic:**
- **Keyword Matching**: Counts occurrences of agent-specific keywords
- **Pattern Overrides**: Special regex patterns for high-confidence routing
- **Fallback**: Routes to general agent when ambiguous

**Agent Selection:**
```
Query: "What's the total pipeline in Iberia?"
  ↓
Keywords matched: ["total", "pipeline", "Iberia"]
  ↓
Routed to: Analytics Agent (fast, 1-step execution)
```

### 2. Specialized Agents

Each agent is optimized for specific query patterns:

#### Analytics Agent
- **Purpose**: Fast metrics and breakdowns
- **Max Steps**: 1 (optimized for speed)
- **Tools**: `pipeline_total`, `count_deals`, `stage_breakdown`, etc.
- **Best For**: "How many...", "What's the total...", "Breakdown by..."

#### Forecasting Agent
- **Purpose**: Time-based predictions
- **Max Steps**: 2
- **Tools**: `weighted_forecast`, `deals_closing_within`, `rolling_monthly_pipeline`
- **Best For**: "Forecast for...", "Deals closing in...", "Monthly trends"

#### Comparison Agent
- **Purpose**: Cross-entity analysis
- **Max Steps**: 2
- **Tools**: `compare_regions`, `top_owners_by_pipeline`, `pipeline_by_opportunity_type`
- **Best For**: "Compare...", "Region vs region", "Top owners"

#### Drilldown Agent
- **Purpose**: Detailed investigation
- **Max Steps**: 3 (allows deeper analysis)
- **Tools**: `top_deals`, `deals_by_stage`, `pipeline_by_owner`, `validate_pipeline_data`
- **Best For**: "Show me deals...", "Which deals in...", "Details about..."

#### Voice Agent
- **Purpose**: Ultra-fast responses for voice
- **Max Steps**: 1 (minimal latency)
- **Tools**: Lightweight subset of most common queries

#### General Agent
- **Purpose**: Complex multi-step queries
- **Max Steps**: 2
- **Tools**: Full toolset
- **Best For**: Ambiguous or multi-faceted queries

### 3. LLM Infrastructure

**Model**: Groq's llama-3.3-70b-versatile via LiteLLM
- **Speed**: 500+ tokens/second
- **Context**: 128K tokens
- **Temperature**: 0.2 (deterministic responses)

**Why Groq?**
- Critical for voice interface (<2s latency requirement)
- Cost-effective for high query volumes
- Excellent reasoning with llama-3.3-70b

### 4. Tool System

Tools are Python functions decorated with `@tool` from smolagents:

```python
@tool
def pipeline_amount(region: str) -> float:
    """Get total pipeline for a region."""
    # Implementation
    return amount
```

**Tool Categories:**
- **Pipeline**: Core metrics (totals, amounts by region)
- **Analytics**: Breakdowns and comparisons
- **Forecasting**: Time-based predictions
- **Owners**: Performance tracking
- **Breakdown**: Drill-down analysis

### 5. Voice Interface (`app/voice_loop.py`)

**Pipeline:**
```
Microphone → VAD → Whisper STT → Agent → TTS → Speakers
```

**Components:**
- **VAD**: webrtcvad for speech detection
- **STT**: faster-whisper (local inference)
- **Wake Word**: Pattern matching for "Buddy..."
- **TTS**: macOS `say` command

**Optimizations:**
- Voice-specific agent with lightweight toolset
- Buffer flushing after each response
- Rate limit handling with graceful recovery

## Data Flow

### Standard Query Flow
```
1. User Query
   ↓
2. Router.route(query)
   ↓
3. Select Specialized Agent
   ↓
4. Agent.run(query)
   ├→ Parse intent
   ├→ Select tool(s)
   ├→ Execute tool(s)
   └→ Format response
   ↓
5. Return natural language answer
```

### Voice Query Flow
```
1. Audio Capture (continuous)
   ↓
2. Voice Activity Detection
   ↓
3. Wake Word Detection
   ↓
4. Speech-to-Text (Whisper)
   ↓
5. Voice Agent.run(query)
   ↓
6. Text-to-Speech
   ↓
7. Buffer Flush + Ready Signal
```

## Key Design Decisions

### Why Multi-Agent vs Single Agent?

**Performance Benefits:**
- Specialized agents have fewer tools → less token overhead
- Faster tool selection (smaller search space)
- Reduced inference time (fewer reasoning steps)

**Quality Benefits:**
- Domain-specific instructions improve accuracy
- Agent specialization reduces hallucinations
- Clear responsibility boundaries

**Maintainability Benefits:**
- Easier to debug and improve individual agents
- Modular architecture allows independent updates
- Clear metrics per agent type

### Token Optimization Strategies

1. **Shortened Docstrings**: Tool descriptions truncated to 180 chars
2. **Minimal Tools per Agent**: Only necessary tools loaded
3. **Concise Instructions**: Agent prompts are terse but effective
4. **No Base Tools**: Disabled smolagents default tools

### Error Handling

**Rate Limiting:**
- Detect rate limit errors in exception messages
- Retry with truncated query (300 chars)
- Return helpful error message with wait time

**Data Errors:**
- Tools return `{"error": "message"}` for invalid inputs
- Agent interprets error and suggests alternatives
- Graceful degradation (never crashes)

**Voice Errors:**
- Background noise filtering via VAD aggressiveness
- Minimum utterance length to ignore blips
- Wake word required to reduce false positives

## Performance Characteristics

### Latency by Agent Type

| Agent Type   | Avg Latency | Max Steps | Tools |
|-------------|-------------|-----------|-------|
| Analytics   | ~1-2s       | 1         | 10    |
| Forecasting | ~2-4s       | 2         | 6     |
| Comparison  | ~2-4s       | 2         | 7     |
| Drilldown   | ~3-6s       | 3         | 9     |
| Voice       | ~1-3s       | 1         | 10    |
| General     | ~2-5s       | 2         | 24    |

### Token Usage

| Component          | Tokens    |
|-------------------|-----------|
| Agent Instructions | ~200      |
| Tool Definitions   | ~150/tool |
| Query + Context    | ~100-500  |
| Response           | ~50-200   |

**Total per Query**: ~2K-8K tokens (depending on complexity)

## Scalability Considerations

### Horizontal Scaling
- Agents are stateless → easy to parallelize
- Tool execution is independent
- Can distribute across multiple API keys

### Caching Opportunities
- Tool results for common queries
- Router decisions for similar patterns
- Whisper model in memory (voice mode)

### Future Optimizations
- Tool result caching layer
- Query result memoization
- Batch processing for analytics queries
- Streaming responses for long analyses

## Security & Privacy

### Production Considerations
- API keys via environment variables
- No credentials in code
- Data filtering in tool layer
- Audit logging for sensitive queries

### Data Protection
- Tools operate on filtered datasets
- No PII in logs
- Region-based access control possible
- Encryption at rest/transit (implementation-specific)

## Monitoring & Observability

### Metrics to Track
- Query routing accuracy
- Agent response times
- Tool execution success rates
- Voice recognition accuracy
- Rate limit incidents

### Debug Mode
```python
run(query, debug=True)
```
Outputs:
- Routing decision
- Selected agent
- Tool calls made
- Execution time

## Extension Points

### Adding New Agents
1. Create agent builder in `app/agents/`
2. Define specialized toolset
3. Add routing keywords/patterns
4. Register in agent registry

### Adding New Tools
1. Implement with `@tool` decorator
2. Add clear docstring (first line critical)
3. Include in relevant agent toolsets
4. Test with sample queries

### Voice Customization
- Adjust `VAD_AGGRESSIVENESS` for environment
- Change `WHISPER_MODEL` for accuracy/speed tradeoff
- Modify wake patterns for different triggers
- Customize TTS voice/rate

## Technology Stack Details

### Core Dependencies
- **smolagents**: Lightweight agentic framework (vs LangChain)
- **LiteLLM**: Unified LLM interface
- **pandas**: Data manipulation
- **faster-whisper**: Efficient STT
- **webrtcvad**: Voice activity detection

### Why smolagents over LangChain?
- Lighter weight (less overhead)
- Simpler abstractions
- Better performance for our use case
- Easier debugging

### Why Groq over OpenAI?
- Speed: 10x faster inference
- Cost: More competitive pricing
- Quality: llama-3.3-70b matches GPT-4 for our tasks
- Control: More predictable rate limits

---

*For implementation details, see the source code and inline comments.*
