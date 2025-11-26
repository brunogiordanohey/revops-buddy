# RevOps Buddy 🎯

**AI-Powered Sales Pipeline Analytics with Voice Interface**

A sophisticated multi-agent AI system for analyzing sales pipeline data through natural language queries. Built with Python, LangChain agents, and voice recognition for hands-free RevOps insights.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 🎥 Demo

[Video Demo Coming Soon]

## ✨ Key Features

- **🤖 Multi-Agent Architecture**: Specialized agents for analytics, forecasting, comparisons, and drill-downs
- **🎙️ Voice Interface**: Natural voice interactions with wake-word detection ("Hey Buddy...")
- **📊 Intelligent Query Routing**: Automatically routes queries to the most appropriate specialized agent
- **💰 Sales Pipeline Analytics**: Comprehensive analysis of deals, forecasts, and owner performance
- **🌍 Multi-Region Support**: Built-in support for regional and country-specific analysis
- **⚡ Fast Inference**: Powered by Groq's lightning-fast LLM infrastructure

## 🏗️ Architecture

RevOps Buddy uses a sophisticated multi-agent system with intelligent query routing:

```
User Query
    ↓
Query Router (analyzes intent)
    ↓
Specialized Agents:
├── Analytics Agent (totals, counts, breakdowns)
├── Forecasting Agent (predictions, trends, timeframes)
├── Comparison Agent (region vs region, owner performance)
├── Drilldown Agent (detailed analysis, top deals)
└── General Agent (complex queries requiring multiple tools)
```

### Agent Specialization

Each agent is optimized for specific query types with curated toolsets:

- **Analytics Agent**: Fast queries about totals, counts, and basic breakdowns (1 step)
- **Forecasting Agent**: Time-based predictions and weighted forecasts (2 steps)
- **Comparison Agent**: Cross-region and owner performance analysis (2 steps)
- **Drilldown Agent**: Detailed analysis with specific deal information (3 steps)
- **Voice Agent**: Lightweight subset for fast voice responses (1 step)

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Groq API key ([get one here](https://console.groq.com))
- macOS (for voice features) or any OS for CLI

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/revops-buddy.git
cd revops-buddy

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up your API key
export GROQ_API_KEY='your-groq-api-key-here'
```

### Usage

**Command Line Mode:**
```python
from app.agent_new import run

# Simple analytics
answer = run("What's the total pipeline in Iberia?")
print(answer)

# Forecasting
answer = run("What's the weighted forecast for next quarter?")
print(answer)

# Comparisons
answer = run("Compare pipeline between Iberia and Latam")
print(answer)
```

**Voice Mode (macOS):**
```bash
python -m app.voice_loop
# Say: "Buddy, what's the total pipeline?"
```

## 📊 Example Queries

### Analytics
- "What's the total pipeline in Iberia?"
- "How many deals are in the Negotiation stage?"
- "Show me a breakdown by stage"
- "What's the average ARR?"

### Forecasting
- "What's the weighted forecast for this month?"
- "How many deals are closing within 30 days?"
- "Show me the rolling 3-month pipeline trend"

### Comparisons
- "Compare Iberia vs Latam pipeline"
- "Who are the top 5 owners by pipeline?"
- "Show me pipeline by opportunity type"

### Drill-downs
- "Show me the top 10 deals"
- "Which deals are in the Procurement/Legal stage?"
- "What's the pipeline for owner John Smith?"

## 🛠️ Tech Stack

- **LLM Framework**: [smolagents](https://github.com/huggingface/smolagents) - Lightweight agentic framework
- **LLM Provider**: [Groq](https://groq.com) - Ultra-fast inference (llama-3.3-70b-versatile)
- **Speech Recognition**: [faster-whisper](https://github.com/guillaumekln/faster-whisper) - Local STT
- **Voice Activity Detection**: [webrtcvad](https://github.com/wiseman/py-webrtcvad)
- **Data Processing**: pandas, numpy
- **Text-to-Speech**: macOS `say` command (built-in)

## 📁 Project Structure

```
revops-buddy/
├── app/
│   ├── agents/           # Specialized agent definitions
│   │   ├── analytics_agent.py
│   │   ├── forecasting_agent.py
│   │   ├── comparison_agent.py
│   │   └── drilldown_agent.py
│   ├── tools/            # Tool implementations (not included in demo)
│   ├── util/             # Utility functions (not included in demo)
│   ├── agent_new.py      # Main agent orchestration
│   ├── router.py         # Intelligent query routing
│   └── voice_loop.py     # Voice interface
├── demo/                 # Simplified demo with mock data
│   └── demo_agent.py     # Standalone demo script
├── requirements.txt
└── README.md
```

## 🎯 Design Decisions

### Why Multi-Agent Architecture?

1. **Performance**: Specialized agents are faster (fewer tools = less token overhead)
2. **Accuracy**: Domain-specific instructions improve response quality
3. **Maintainability**: Each agent has clear responsibilities
4. **Scalability**: Easy to add new specialized agents

### Why Groq?

- **Speed**: 500+ tokens/second inference (critical for voice UX)
- **Cost**: Competitive pricing for production use
- **Quality**: Excellent performance with llama-3.3-70b

### Query Routing Strategy

The router uses keyword matching with smart override rules:
- Fast keyword scoring for initial classification
- Pattern-based overrides for high-confidence cases
- Fallback to general agent for ambiguous queries

## 🔐 Security Notes

This repository contains a **simplified demo** version. The production version includes:
- Proprietary data processing utilities
- Custom normalization logic for internal data formats
- Business-specific tool implementations
- Real sales pipeline data loading

For demo purposes, this repo uses mock data and simplified tools.

## 🚧 Roadmap

- [ ] Web interface (Streamlit/Gradio)
- [ ] Docker containerization
- [ ] Extended voice commands
- [ ] Export to reports/dashboards
- [ ] Multi-language support
- [ ] Integration with CRM APIs (Salesforce, HubSpot)

## 📝 License

MIT License - see LICENSE file for details

## 🤝 Contributing

This is a portfolio project, but suggestions and feedback are welcome! Feel free to open issues or submit PRs.

## 👤 Author

**brugiordano**
- Building AI-powered business intelligence tools
- Focus: Multi-agent systems, voice interfaces, and LLM applications
- [https://www.linkedin.com/in/brunogiordano11/](#) | [brugiordano.com](#)

## 🙏 Acknowledgments

- [Groq](https://groq.com) for blazing-fast inference
- [Hugging Face](https://huggingface.co) for smolagents framework
- The open-source AI community

---

*Built as a proof-of-concept for AI-powered RevOps analytics. Showcasing multi-agent architecture, query routing, and voice interface design.*
