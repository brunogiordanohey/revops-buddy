# Setup & Usage Guide

## Prerequisites

- **Python 3.11+** (3.11 or 3.12 recommended)
- **Groq API Key** (free tier available at https://console.groq.com)
- **macOS** (for voice features) or any OS for CLI usage

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/revops-buddy.git
cd revops-buddy
```

### 2. Create Virtual Environment

```bash
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

For voice features on macOS, the dependencies should install without issues. On other platforms, you may skip voice-related packages.

### 4. Set Up API Key

```bash
# Option 1: Environment variable (temporary)
export GROQ_API_KEY='your-groq-api-key-here'

# Option 2: .env file (persistent)
echo "GROQ_API_KEY=your-groq-api-key-here" > .env
```

Get your free Groq API key:
1. Visit https://console.groq.com
2. Sign up / log in
3. Navigate to API Keys
4. Create a new key

## Quick Start - Demo Mode

Run the demo with mock data (no real pipeline required):

```bash
python demo/demo_agent.py
```

This will:
1. Initialize all agents with mock data
2. Run several example queries
3. Enter interactive mode for custom queries

**Example output:**
```
🚀 Initializing RevOps Buddy agents...
✅ Agents initialized!

❓ What's the total pipeline?
[DEBUG] Routing to: analytics
💡 The total pipeline is €2.9M

❓ Compare Iberia vs Latam
[DEBUG] Routing to: comparison
💡 Iberia has €900K and Latam has €2.0M in pipeline
```

## Production Usage

### Basic Python Usage

```python
from app.agent_new import run

# Simple query
answer = run("What's the total pipeline in Iberia?")
print(answer)

# With debug info
answer = run("Compare regions", debug=True)
print(answer)

# Voice mode (faster, lightweight)
answer = run("What's the forecast?", mode="voice")
print(answer)
```

### Setting Up Your Data

The production version expects tools to load data from your CRM or data warehouse. You'll need to implement:

1. **Data Loading** (`app/util/loaders.py`)
   ```python
   import pandas as pd
   
   def load_df() -> pd.DataFrame:
       """Load your pipeline data."""
       # Load from CSV, database, API, etc.
       df = pd.read_csv("pipeline_data.csv")
       # Add required columns if missing
       return df
   ```

2. **Tool Implementations** (`app/tools/`)
   - See `demo/demo_agent.py` for simplified examples
   - Full production tools are in the gitignored `app/tools/` directory
   - Required columns: `Amount`, `Stage`, `Region_norm`, `Close_Date`, etc.

### Required Data Schema

Your pipeline data should have these columns:

| Column | Type | Description |
|--------|------|-------------|
| `Deal_Name` | str | Opportunity name |
| `Amount` | float | Deal value |
| `Stage` | str | Current stage |
| `Region_norm` | str | Normalized region (iberia, latam, etc.) |
| `Country` | str | Country name |
| `Owner` | str | Deal owner |
| `Probability` | float | Win probability (0-100) |
| `Close_Date` | date | Expected close date |
| `ARR` | float | Annual recurring revenue |

Optional columns: `Opportunity_Type`, `Customer_Type`, `Currency`

## Voice Interface

### macOS Setup

Voice features work out of the box on macOS:

```bash
python -m app.voice_loop
```

The system will:
1. Listen continuously for the wake word "Buddy"
2. Transcribe your question
3. Route to appropriate agent
4. Speak the answer back

**Example interaction:**
```
You: "Buddy, what's the total pipeline?"
Buddy: "The total pipeline is 2.9 million euros"
🎧 Ready for next question
```

### Voice Configuration

Edit `app/voice_loop.py` to customize:

```python
# Speech recognition
WHISPER_MODEL = "medium.en"  # Change to "large-v3" for better accuracy
WHISPER_LANGUAGE = "en"      # Change to "es" for Spanish

# Voice output
VOICE_NAME = "Samantha"      # Run `say -v ?` to list voices
VOICE_RATE = "200"           # Words per minute

# Detection sensitivity
VAD_AGGRESSIVENESS = 2       # 0-3, higher = more aggressive filtering
MIN_UTTERANCE_S = 0.6        # Ignore short sounds
SILENCE_GAP_S = 0.6          # How long to wait before processing
```

### Linux/Windows Voice Setup

Voice features require additional setup on non-macOS platforms:

1. **Replace TTS**: Swap `say` command with `pyttsx3` or `gTTS`
2. **Test Audio**: Ensure microphone permissions are granted
3. **Adjust VAD**: May need different aggressiveness settings

## Advanced Usage

### Custom Agent Configuration

```python
from app.agent_new import initialize_agents
from app.agents import build_analytics_agent

# Define your custom tools
from smolagents import tool

@tool
def my_custom_metric() -> float:
    """Calculate custom business metric."""
    return 42.0

# Initialize with custom toolset
custom_tools = [my_custom_metric, ...]
initialize_agents(all_tools=custom_tools, voice_tools=[...])
```

### Batch Processing

```python
from app.agent_new import run

queries = [
    "What's the total pipeline?",
    "Show top 5 deals",
    "Compare regions",
]

results = []
for query in queries:
    answer = run(query)
    results.append({"query": query, "answer": answer})

# Save to file, send to Slack, etc.
```

### Custom Router Logic

Edit `app/router.py` to add new routing patterns:

```python
# Add keywords for custom agent type
CUSTOM_KEYWORDS = ["special", "unique", "custom"]

# Add to route() method
if self._count_matches(q_lower, self.CUSTOM_KEYWORDS) > 0:
    return 'custom_agent'
```

## Troubleshooting

### "GROQ_API_KEY not set"

```bash
# Verify key is set
echo $GROQ_API_KEY

# If empty, set it
export GROQ_API_KEY='your-key'

# Or use .env file
pip install python-dotenv
echo "GROQ_API_KEY=your-key" > .env
```

### "Rate limit exceeded"

Groq free tier limits:
- 30 requests per minute
- 14,400 requests per day

Wait 20-30 seconds and retry. The system handles this automatically in voice mode.

### "Model not found" or "Permission denied"

Ensure you're using the correct model string:
```python
model_id="groq/llama-3.3-70b-versatile"
```

If issues persist, check your Groq console for model access.

### Voice: "No audio detected"

1. Check microphone permissions
2. Increase `VAD_AGGRESSIVENESS` to 3
3. Decrease `MIN_UTTERANCE_S` to 0.3
4. Test microphone: `python -c "import sounddevice; print(sounddevice.query_devices())"`

### Voice: "Too many false triggers"

1. Decrease `VAD_AGGRESSIVENESS` to 1
2. Increase `MIN_UTTERANCE_S` to 0.8
3. Use more specific wake word patterns

## Performance Tuning

### Speed Optimization

```python
# Use voice agent for fastest responses
answer = run(query, mode="voice")

# Reduce max_steps in agent builders
analytics_agent = build_analytics_agent(model, max_steps=1)

# Use specific queries (bypass routing)
from app.tools.pipeline import pipeline_total
result = pipeline_total()
```

### Memory Optimization

```python
# Load Whisper model only when needed
# Unload after use if memory-constrained

# Use smaller Whisper model
WHISPER_MODEL = "base.en"  # vs "medium.en" or "large-v3"
```

### Cost Optimization

- Cache common queries
- Use voice agent (fewer tokens)
- Batch similar queries
- Consider self-hosting Whisper STT

## Next Steps

- **Production Deployment**: See `docs/DEPLOYMENT.md`
- **Architecture Deep-Dive**: See `docs/ARCHITECTURE.md`
- **Contributing**: See `CONTRIBUTING.md`
- **Custom Tools**: See `docs/TOOLS.md`

## Getting Help

- **Issues**: Open a GitHub issue
- **Discussions**: Use GitHub Discussions
- **Email**: [hello@brugiordano.com]

---

*Happy querying! 🎯*
