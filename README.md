# Telegram Chatbot Multi-Account & Digital Clone

An advanced, multi-account Telegram Userbot powered by a multi-agent digital clone AI pipeline. The system runs concurrent userbot personas (e.g., Alya, Intan), responds to messages automatically using customizable personality constraints, handles payments for VIP upgrades, and offers an administrative control bot for the bot owner.

---

## 🚀 Key Features

*   **Multi-Account Userbot Service**: Manage and run multiple Telethon userbot accounts concurrently in a single event loop.
*   **Digital Clone AI Pipeline**: A multi-stage agentic pipeline that ensures responses sound authentic:
    *   **Context Agent**: Gathers chat details, date, time, and latest history.
    *   **Memory Agent**: Manages and persists long-term details about users.
    *   **Personality Agent**: Applies custom persona files, slang lists, and rules.
    *   **Response Agent**: Drafts the initial reply.
    *   **Critic Agent**: Refines grammar, tone, and length before sending.
    *   **Confidence Agent**: Decides if a response should be auto-sent or held for review based on a confidence threshold.
*   **Owner Bot (Manage Bot)**: Administrative Telegram bot to control userbots, check payment status, view stats, and configure VIP pricing.
*   **SQLite Storage**: Unified DB for user logs, chat histories, active payments, account registries, and media mappings.

---

## 📁 Directory Structure

```text
├── agents/                  # Multi-agent digital clone pipeline
│   ├── base.py              # Base Agent classes
│   ├── confidence_agent.py  # Confidence validation agent
│   ├── context_agent.py     # Context aggregation agent
│   ├── critic_agent.py      # Output editor & check agent
│   ├── memory_agent.py      # User relationships & facts memory agent
│   ├── personality_agent.py # Persona & slang style-guide compiler agent
│   ├── pipeline.py          # Orchestrates execution of the agents
│   └── stage_agent.py       # Manages sales funnel/user stages
│
├── docs/                    # System documentation
│   ├── ARCHITECTURE.md      # High-level architecture and flowcharts
│   ├── GOALS.md             # Project roadmap & remaining milestones
│   └── agents_architecture.md # Details about the digital clone agents
│
├── prompts/                 # Core AI prompt templates
│   ├── larangan.txt         # negative constraints (rules on what NOT to say)
│   ├── persona.txt          # default character profile
│   ├── sales.txt            # pricing and upgrade instructions
│   └── slang.txt            # conversational vocabulary (casual slangs)
│
├── scripts/                 # Utility scripts & simulations
│   ├── inspect_saved.py     # Helper to scan saved messages for media file_ids
│   ├── reviewer.py          # LLM Conversation Reviewer tool
│   ├── simulator.py         # Runs simulated chats between two AI models
│   └── tester.py            # AI-driven conversation tester & scenarios
│
├── tests/                   # Automated unit & functional tests
│   └── test_chatbot.py      # Pytest suite running functional logic tests
│
├── account_manager.py       # Coordinates Telethon userbot connections and events
├── ai_engine.py             # Interfaces with LLM APIs (Groq/OpenRouter)
├── clients.py               # Centralized HTTP & LLM client initialization
├── db.py                    # Database schema, operations, and legacy migrations
├── env_loader.py            # Parses and validates environment variables
├── main.py                  # Main entry point (starts userbots & admin bot)
├── manage_bot.py            # Admin/Owner Telegram bot handlers
├── media_handler.py         # Automates photo/video mappings for userbot responses
├── media_manager.py         # High-level media dispatching and tracking logic
├── user_tracker.py          # Keeps track of user funnel progress (e.g. asked_price)
├── requirements.txt         # Project package dependencies
└── .gitignore               # Ignored files (secrets, database, local logs)
```

---

## 🛠️ Setup & Installation

### 1. Prerequisites
*   Python 3.10+
*   Git

### 2. Clone the Repository
```bash
git clone https://github.com/nealmtroy/chatbot.git
cd chatbot
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configuration
Create a `.env` file in the root directory (you can copy `.env.example` as a starting point) and populate it with your keys:
```env
# AI Provider
SELECTED_PROVIDER=groq  # or openrouter
GROQ_API_KEY=your_groq_api_key
OPENROUTER_API_KEY=your_openrouter_api_key

# Telegram Credentials
TELEGRAM_API_ID=your_telegram_api_id
TELEGRAM_API_HASH=your_telegram_api_hash

# Admin / Owner Bot
MANAGE_BOT_TOKEN=your_owner_bot_token
```

---

## 🚀 Usage

### Running the Application
Start the unified process (Userbots and Owner Bot in one event loop):
```bash
python main.py
```

### Running Simulations
You can run a simulated conversation between a dummy user (defined in `scripts/tester.py`) and your digital clone:
```bash
# To list available scenarios
python scripts/simulator.py

# To run Scenario 1
python scripts/simulator.py --scenario 1
```

### Running Tests
Execute the unit tests using `pytest` to verify the codebase integrity:
```bash
pytest tests/test_chatbot.py
```
