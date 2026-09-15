"""
core/discord_tools.py — Discord-specific tools for agentAI runtime.
These tools provide Discord-aware functionality to the AI agent.

All response data is loaded from data/nepali_responses.json via
core.data_loader so it can be edited without touching Python code.
"""
import random
from datetime import datetime
from typing import Dict, Any

from core.data_loader import get_data


def _responses() -> dict:
    """Shorthand for the Nepali responses data."""
    return get_data("nepali_responses")


def get_nepali_greeting(args: Dict[str, Any]) -> Dict[str, str]:
    """Generate a contextual Nepali greeting based on time of day."""
    hour = datetime.now().hour
    data = _responses().get("greetings", {})

    if 5 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 17:
        period = "afternoon"
    elif 17 <= hour < 21:
        period = "evening"
    else:
        period = "night"

    period_data = data.get(period, {"formal": "Namaste", "casual": "namaste"})
    formal = period_data["formal"]
    casual = period_data["casual"]

    templates = data.get("templates", ["{formal}! K cha hajur?"])
    greeting_text = random.choice(templates).format(formal=formal, casual=casual)

    return {
        "greeting": greeting_text,
        "time_of_day": formal,
        "hour": hour
    }


def get_nepali_slang_response(args: Dict[str, Any]) -> Dict[str, str]:
    """Generate appropriate Nepali slang responses based on context."""
    context = args.get("context", "general")
    responses = _responses().get("slang_responses", {})
    context_responses = responses.get(context, responses.get("general", ["Ke garne yaar"]))
    return {
        "response": random.choice(context_responses),
        "context": context
    }


def nepali_number_converter(args: Dict[str, Any]) -> Dict[str, Any]:
    """Convert numbers to Nepali text representation."""
    number = args.get("number", 0)
    numbers_map = _responses().get("nepali_numbers", {})

    result = numbers_map.get(str(int(number)), str(number))

    return {
        "original": number,
        "nepali": result,
        "devanagari": result.split(" ")[0] if " " in result else result
    }


def nepali_day_converter(args: Dict[str, Any]) -> Dict[str, str]:
    """Convert day info to Nepali."""
    day_num = datetime.now().weekday()
    data = _responses()
    nepali_days = data.get("nepali_days", [])
    english_days = data.get("english_days", [])

    nepali = nepali_days[day_num] if day_num < len(nepali_days) else "Unknown"
    english = english_days[day_num] if day_num < len(english_days) else "Unknown"

    return {
        "day": nepali,
        "day_number": day_num,
        "english": english
    }


def get_nepali_proverb(args: Dict[str, Any]) -> Dict[str, str]:
    """Get a random Nepali proverb with English translation."""
    proverbs = _responses().get("proverbs", [])
    if not proverbs:
        return {
            "nepali": "N/A",
            "transliteration": "N/A",
            "english": "No proverbs loaded",
            "meaning": "Check data/nepali_responses.json"
        }
    return random.choice(proverbs)


def discord_context_awareness(args: Dict[str, Any]) -> Dict[str, Any]:
    """Provide Discord context-aware responses."""
    context_type = args.get("context_type", "general")
    contexts = _responses().get("discord_contexts", {})
    responses = contexts.get(context_type, contexts.get("general", ["K cha khabar?"]))
    return {
        "response": random.choice(responses),
        "context_type": context_type
    }


def latin_nepali_translator(args: Dict[str, Any]) -> Dict[str, str]:
    """
    Simple Latin Nepali phrase translator.
    This provides common phrases in Latin Nepali script.
    """
    english = args.get("english", "").lower().strip()
    translations = _responses().get("translations", {})
    translated = translations.get(english, english)  # Return original if no translation
    return {
        "english": english,
        "latin_nepali": translated,
        "found": english in translations
    }


# Register function to add all Discord tools to the runtime
def register_discord_tools(runtime):
    """Register all Discord-specific tools with the AgentAI runtime."""
    
    runtime.register_custom_tool(
        name="nepali_greeting",
        handler=get_nepali_greeting,
        description="Generate a contextual Nepali greeting based on time of day",
        args_schema={
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    
    runtime.register_custom_tool(
        name="nepali_slang",
        handler=get_nepali_slang_response,
        description="Generate appropriate Nepali slang responses based on context",
        args_schema={
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "enum": ["agreement", "disagreement", "confusion", "excitement", "general"],
                    "description": "Context for the slang response"
                }
            },
            "required": []
        }
    )
    
    runtime.register_custom_tool(
        name="nepali_number",
        handler=nepali_number_converter,
        description="Convert numbers to Nepali text representation",
        args_schema={
            "type": "object",
            "properties": {
                "number": {
                    "type": "number",
                    "description": "Number to convert (0-10 for full Nepali text)"
                }
            },
            "required": ["number"]
        }
    )
    
    runtime.register_custom_tool(
        name="nepali_day",
        handler=nepali_day_converter,
        description="Get current day in Nepali",
        args_schema={
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    
    runtime.register_custom_tool(
        name="nepali_proverb",
        handler=get_nepali_proverb,
        description="Get a random Nepali proverb with English translation",
        args_schema={
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    
    runtime.register_custom_tool(
        name="discord_context",
        handler=discord_context_awareness,
        description="Provide Discord context-aware responses for different topics",
        args_schema={
            "type": "object",
            "properties": {
                "context_type": {
                    "type": "string",
                    "enum": ["gaming", "study", "music", "tech", "general"],
                    "description": "Type of context for response"
                }
            },
            "required": []
        }
    )
    
    runtime.register_custom_tool(
        name="latin_nepali",
        handler=latin_nepali_translator,
        description="Translate common English phrases to Latin Nepali",
        args_schema={
            "type": "object",
            "properties": {
                "english": {
                    "type": "string",
                    "description": "English phrase to translate"
                }
            },
            "required": ["english"]
        }
    )