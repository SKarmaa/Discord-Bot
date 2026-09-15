# agentAI Integration Guide

## Overview

The Discord bot has been enhanced with **agentAI** - a new AI runtime system inspired by Agentic AI architecture, featuring advanced tool calling, performance optimization, and intelligent Latin Nepali language support.

## Key Features

### 🤖 Enhanced AI Runtime
- **Tool Calling**: The AI can now use tools to perform calculations, get current time, analyze text, and provide Nepali context-aware responses
- **Performance Engine**: Built-in caching with hit/miss tracking and connection pooling for faster responses
- **Language Detection**: Automatic detection of English, Latin Nepali, and Devanagari Nepali input
- **Better Architecture**: Structured runtime system but optimized for Discord

### 🇳🇵 Advanced Nepali Support
- **Language-Aware Responses**: Automatically detects input language and responds appropriately
- **Natural Nepali Personality**: Enhanced personality
- **Context-Aware Responses**: Different responses for gaming, study, music, tech, and general contexts
- **Nepali Tools**: Specialized tools for Nepali greetings, proverbs, number conversion, day translation, and language detection
- **Translation**: Built-in English to Latin Nepali phrase translator

### ⚡ Performance Features
- **Smart Caching**: Response caching with performance statistics (hit rate, cache size)
- **Connection Pooling**: Reuses HTTP connections for API calls
- **Source Tracking**: Automatically extracts and cites sources from tool results
- **Structured Metadata**: Comprehensive response tracking including language, execution time, and cache status

### 🔧 Available Tools

#### Default Tools:
- `calculator` - Safe mathematical calculations
- `current_time` - Get current date and time
- `text_analysis` - Analyze word count, character count, etc.

#### Nepali Tools:
- `nepali_greeting` - Contextual Nepali greetings based on time of day
- `nepali_slang` - Appropriate Nepali slang responses
- `nepali_number` - Convert numbers to Nepali text
- `nepali_day` - Get current day in Nepali
- `nepali_proverb` - Random Nepali proverbs with translations
- `discord_context` - Discord context-aware responses
- `latin_nepali` - English to Latin Nepali translation
- `detect_language` - Automatic language detection

## Usage

### Commands
- `/ai <question>` - Ask agentAI a question (with tool support)
- `/aistatus` - Check agentAI status, available tools, cache performance, and language support
- `oh kp baa <question>` - Trigger phrase for AI (legacy support)

### Example Interactions

**Math with Tools:**
```
User: /ai What is 17 multiplied by 23?
agentAI: Let me calculate that for you. 🔧 *Used tools: calculator*
The result is 391. Tyo simple math ho!
```

**Language Detection:**
```
User: /ai नमस्ते, के छ?
agentAI: 🔧 *Language detected: Nepali Devanagari*
नमस्ते! के छ हजुर? म कस्तो सहयोग गर्न सक्छु?
```

**Nepali Context:**
```
User: /ai Give me a Nepali greeting
agentAI: 🔧 *Used tools: nepali_greeting*
शुभ दिउँसो (Good afternoon)! K cha hajur?
```

**Translation:**
```
User: /ai How do you say "thank you" in Nepali?
agentAI: 🔧 *Used tools: latin_nepali*
"Thank you" in Nepali is "dhanyabaad"
```

**Discord Context:**
```
User: /ai What's up for gaming today?
agentAI: 🔧 *Used tools: discord_context*
Yo game kya hai bro? PUBG hola? Gaming garne time ho, rank k cha?
```

## Architecture

### Core Components

1. **AgentAIRuntime** (`core/agentai_runtime.py`)
   - Main AI runtime with tool calling capabilities
   - Performance engine with caching and statistics
   - Language detection and processing
   - Tool registry and execution
   - Source tracking and metadata
   - Singleton pattern for bot-wide instance

2. **Discord Tools** (`core/discord_tools.py`)
   - Nepali-specific tools and functions
   - Discord context-aware responses
   - Latin Nepali translation utilities
   - Language detection helpers

3. **Performance Engine**
   - Response caching (5-minute TTL)
   - Connection pooling for API calls
   - Cache statistics (hits, misses, hit rate)
   - Automatic cache management

### Architecture

The architecture draws several key concepts:

- **Language Detection**: Automatic detection of English/Nepali (Latin & Devanagari) input
- **Tool-First Philosophy**: "Tool results > memory" - tools are the source of truth for changing information
- **Structured Responses**: Comprehensive metadata including language, sources, and performance metrics
- **Performance Optimization**: Caching with statistics and connection pooling
- **Authentic Personality**: Natural Nepali personality without corporate chatbot feel

### Integration Points

- **cogs/ai_commands.py** - Updated to use AgentAIRuntime with enhanced status display
- **cogs/events.py** - Updated message handler to use AgentAIRuntime

## Configuration

No additional configuration needed. The system uses existing:
- `GEMINI_API_KEY` from .env file
- `ai_cooldown_minutes` from features.json
- `ai_trigger_phrase` from features.json

## Performance Improvements

1. **Smart Caching**: Identical prompts within 5 minutes return cached responses with hit rate tracking
2. **Connection Pooling**: Reuses HTTP connections for API calls
3. **Tool Optimization**: Tools execute locally without API calls
4. **Structured Responses**: Better error handling and comprehensive status tracking
5. **Language Processing**: Automatic language detection reduces processing overhead

## Extending agentAI

### Adding Custom Tools

```python
# In your code, get the runtime instance
from core.agentai_runtime import get_agentai_runtime

runtime = get_agentai_runtime()

# Register a custom tool
def my_custom_tool(args):
    return {"result": "custom functionality", "source": "custom_tool"}

runtime.register_custom_tool(
    name="my_tool",
    handler=my_custom_tool,
    description="Description of what the tool does",
    args_schema={
        "type": "object",
        "properties": {
            "param": {"type": "string"}
        },
        "required": ["param"]
    }
)
```

### Accessing Runtime Statistics

```python
runtime = get_agentai_runtime()
stats = runtime.get_runtime_stats()
print(f"Cache hit rate: {stats['cache_stats']['hit_rate']:.1%}")
print(f"Total tools: {stats['total_tools']}")
```

### Modifying System Prompt

The system prompt is defined in `AgentAIRuntime._build_system_prompt()`. The current prompt has authentic Nepali personality. You can modify it to change the AI's personality while maintaining the Latin Nepali focus.

## Comparison with Old System

| Feature | Old System | New agentAI |
|---------|-----------|-------------|
| API Calls | Direct Gemini API | Structured runtime with caching |
| Tool Support | None | 11+ built-in tools |
| Nepali Support | Basic KP Oli persona | Rich Latin Nepali with language detection |
| Performance | Standard | Caching + connection pooling + statistics |
| Language Detection | None | Automatic English/Latin Nepali/Devanagari |
| Source Tracking | None | Automatic source extraction from tools |
| Extensibility | Limited | Easy tool registration |
| Error Handling | Basic | Comprehensive with status tracking |

## Troubleshooting

### Tools not working
- Check that the tool is properly registered
- Verify tool schema is correct JSON Schema format
- Check bot logs for tool execution errors

### Caching issues
- Cache automatically expires after 5 minutes
- Can be cleared programmatically if needed
- Check cache statistics via `/aistatus`
- Cached responses are based on prompt + tool context hash

### Language detection issues
- The system detects Devanagari characters and common Nepali words
- Use Nepali-specific tools to force Nepali content
- Language directive is automatically added to system prompt

### Nepali responses not appearing
- The AI naturally uses Nepali based on detected language
- Use Nepali-specific tools to force Nepali content
- System prompt encourages Latin Nepali usage
- Language detection is automatic based on input

## Performance Monitoring

Use `/aistatus` to monitor:
- Cache hit rate and statistics
- Available tools and their status
- Current model and configuration
- Language support status

