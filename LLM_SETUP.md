# LLM Setup Guide

HPC Pilot supports natural-language intent planning using LLM tool-calling. This document covers configuring Anthropic Claude and OpenAI ChatGPT.

## Supported LLMs

| Provider | Model Family | API Key Env Var |
|----------|-------------|-----------------|
| Anthropic | Claude 3.x | `ANTHROPIC_API_KEY` |
| OpenAI | GPT-4o, GPT-4 Turbo | `OPENAI_API_KEY` |

## Quick Start

### Anthropic Claude

```bash
pip install -e ".[anthropic]"

export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-api03-...

hpc-pilot shell
# Now you can say: "give alice 48 hours of wall time on the gpu qos"
```

### OpenAI ChatGPT

```bash
pip install -e ".[openai]"

export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-proj-...

hpc-pilot shell
```

## How It Works

1. **Intent** → LLM receives your natural language request
2. **Tool schemas** → LLM sees available HPC-Pilot tools
3. **Plan** → LLM generates ordered tool calls with arguments
4. **Safety** → All generated plans go through approval gates
5. **Execution** → Plan steps execute with dry-run, approval, apply

## Configuration Examples

### Multiple Providers (CLI)

Switch providers per-command:

```bash
# Use Claude
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... hpc-pilot plan "install 4 GPU nodes"

# Use ChatGPT
LLM_PROVIDER=openai OPENAI_API_KEY=... hpc-pilot plan "extend user wall time"
```

### Multiple Providers (Environment)

Set default provider in your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-api03-...
export HPC_DB_URL=postgresql+psycopg://hpcagent@localhost/hpc_agent
export HPC_CONFIG_REPO=/etc/hpc-pilot/config
```

### Disable LLM

For CLI-only operation or testing:

```bash
export LLM_PROVIDER=mock
```

## LLM Input/Output Contract

### Input (to LLM)

- **System prompt**: Safety contract, tool list, cluster context
- **User message**: "Intent" (natural language task)
- **Tools**: JSON schemas from all registered HPC-Pilot tools

### Output (from LLM)

The LLM must return tool calls in this format:

```json
{
  "tool_calls": [
    {
      "name": "slurm.manage_qos",
      "arguments": {
        "name": "gpu",
        "op": "modify",
        "max_wall_min": 2880
      }
    }
  ]
}
```

HPC-Pilot parses tool calls into a dependency-ordered `Plan`.

## Troubleshooting

### LLM returns content instead of tool calls

Some LLMs default to text responses. HPC-Pilot will raise `NotImplementedError` if tool calls aren't present. Ensure your LLM is configured for tool-calling mode.

**Fix**: Use models with strong tool-calling support:
- Claude 3.5 Sonnet or newer
- GPT-4o or GPT-4 Turbo

### Rate limits or API errors

```bash
# Check API key
echo $ANTHROPIC_API_KEY  # or $OPENAI_API_KEY

# Test connectivity
python3 -c "from anthropic import Anthropic; print(Anthropic().models.list())"
python3 -c "from openai import OpenAI; print(OpenAI().models.list())"
```

### Provider not found

```bash
# Verify provider is recognized
python3 -c "from hpc_agent.core.llm import get_llm_provider; print(get_llm_provider())"
```

## Cost Management

| Action | Cost Estimate |
|--------|---------------|
| Small intent | $0.001-$0.005 |
| Complex plan (10+ steps) | $0.01-$0.05 |
| Platform: Claude 3.5 Sonnet | 1M tokens ≈ $3 input / $15 output |
| Platform: GPT-4o | 1M tokens ≈ $5 input / $20 output |

Enable use with:
```bash
export HPC_MAX_BLAST_RADIUS_AUTO=2  # Smaller plans = lower cost
export DRY_RUN_DEFAULT=true         # Always preview first
```

## Development: Custom LLM Provider

Implement the `LLMProvider` interface in `hpc_agent/core/llm.py`:

```python
from hpc_agent.core.llm import LLMProvider, LLMMessage, ToolSchema, LLMResponse, Plan

class CustomLLM(LLMProvider):
    def __init__(self, model: str = "custom Model"):
        # Initialize your custom client
        ...
    
    def call(self, messages, tools=None, system_prompt=None) -> LLMResponse:
        # Return tool_calls list
        ...
    
    def plan(self, intent: str, tools: list[ToolSchema]) -> Plan:
        # Build Plan from tool calls
        ...

# Register in get_llm_provider():
def get_llm_provider() -> LLMProvider:
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    
    if provider == "anthropic":
        return AnthropicLLM()
    elif provider == "openai":
        return OpenAILLM()
    elif provider == "custom":
        return CustomLLM()
    ...
```
