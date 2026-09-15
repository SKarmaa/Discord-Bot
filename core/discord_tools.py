"""
core/discord_tools.py — Discord-specific tools for agentAI runtime.
These tools provide Discord-aware functionality to the AI agent.
"""
import random
from datetime import datetime
from typing import Dict, Any


def get_nepali_greeting(args: Dict[str, Any]) -> Dict[str, str]:
    """Generate a contextual Nepali greeting based on time of day."""
    hour = datetime.now().hour
    
    if 5 <= hour < 12:
        time_greeting = "शुभ प्रभात (Good morning)"
        casual = "suva prabhat"
    elif 12 <= hour < 17:
        time_greeting = "शुभ दिउँसो (Good afternoon)"
        casual = "suwa diusso"
    elif 17 <= hour < 21:
        time_greeting = "शुभ साँझ (Good evening)"
        casual = "suwa sanja"
    else:
        time_greeting = "शुभ रात्री (Good night)"
        casual = "suwa ratri"
    
    nepali_greetings = [
        f"{time_greeting}! K cha hajur?",
        f"{casual} bhai! Ke garne?",
        "Namaste! Kasto cha?",
        "Hello ji! K thyo cha?",
        f"{time_greeting} yaar! K cha?"
    ]
    
    return {
        "greeting": random.choice(nepali_greetings),
        "time_of_day": time_greeting,
        "hour": hour
    }


def get_nepali_slang_response(args: Dict[str, Any]) -> Dict[str, str]:
    """Generate appropriate Nepali slang responses based on context."""
    context = args.get("context", "general")
    
    responses = {
        "agreement": [
            "Hau hau, totally agree!",
            "Ekdam thik cha!",
            "Huncha ni bro",
            "Tyo ta thik ho!",
            "Bilkul bhai!"
        ],
        "disagreement": [
            "Eh, ke bhannu hai yo?",
            "Malai thik lagena ni",
            "Kta/ktis, yo ta gardaina hai",
            "Aru bhannu na",
            "Haina bro, aru thik cha"
        ],
        "confusion": [
            "Ke bhanyo hai? Maile bujhina",
            "Khoi, kura clear gar na",
            "Ke garne, confusion cha",
            "Aile bujheina, again bhan",
            "Wait, ke kura?"
        ],
        "excitement": [
            "Yo wa! Ekdam kamaal!",
            "Jhakkas! Kya baat hai!",
            "Waah! Dherai ramro!",
            "Kamaal! Game strong!",
            "Ekdum! Fatafat!"
        ],
        "general": [
            "Ke garne yaar",
            "Hau, k cha khabar?",
            "Thik cha, continue gar",
            "Huncha ni",
            "Aile ke garnu"
        ]
    }
    
    context_responses = responses.get(context, responses["general"])
    return {
        "response": random.choice(context_responses),
        "context": context
    }


def nepali_number_converter(args: Dict[str, Any]) -> Dict[str, Any]:
    """Convert numbers to Nepali text representation."""
    number = args.get("number", 0)
    
    # Simple mapping for common numbers
    nepali_numbers = {
        0: "शून्य (zero)",
        1: "एक (ek)",
        2: "दुई (dui)",
        3: "तीन (teen)",
        4: "चार (char)",
        5: "पाँच (paanch)",
        6: "छ (chha)",
        7: "सात (saat)",
        8: "आठ (aath)",
        9: "नौ (nau)",
        10: "दश (dash)"
    }
    
    if number in nepali_numbers:
        result = nepali_numbers[number]
    else:
        result = str(number)  # Return as-is for larger numbers
    
    return {
        "original": number,
        "nepali": result,
        "devanagari": result.split(" ")[0] if " " in result else result
    }


def nepali_day_converter(args: Dict[str, Any]) -> Dict[str, str]:
    """Convert day info to Nepali."""
    day_num = datetime.now().weekday()
    
    nepali_days = [
        "आइतबार (Aaitabar)",
        "सोमबार (Sombar)",
        "मंगलबार (Mangalbar)",
        "बुधबार (Budhabar)",
        "बिहिबार (Bihibar)",
        "शुक्रबार (Shukrabar)",
        "शनिबार (Sanibar)"
    ]
    
    return {
        "day": nepali_days[day_num],
        "day_number": day_num,
        "english": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][day_num]
    }


def get_nepali_proverb(args: Dict[str, Any]) -> Dict[str, str]:
    """Get a random Nepali proverb with English translation."""
    proverbs = [
        {
            "nepali": "जस्तो सिङ्गो उस्तो काग",
            "transliteration": "Jasto singo usto kaag",
            "english": "Like beak, like crow (birds of a feather flock together)",
            "meaning": "People with similar character stick together"
        },
        {
            "nepali": "एकान्ते राम्रो, बहुतान्ते गाह्रो",
            "transliteration": "Ekante ramro, bahutante gahro",
            "english": "Good when alone, difficult when many",
            "meaning": "Too many cooks spoil the broth"
        },
        {
            "nepali": "ढोको लागे गाज्रो",
            "transliteration": "Dhoko laage gaajro",
            "english": "When door closes, brinjal",
            "meaning": "Too late to take action"
        },
        {
            "nepali": "हिरोद्धो गर्दै मासु खाने",
            "transliteration": "Hiroddho gardai maasu khane",
            "english": "Eating meat while sharpening knife",
            "meaning": "Doing something hastily without proper preparation"
        },
        {
            "nepali": "आफ्नै मुखले आफ्नै हात काट्ने",
            "transliteration": "Aafnai mukhale aafnai haat katne",
            "english": "Cutting one's own hand with one's mouth",
            "meaning": "Self-destructive behavior"
        }
    ]
    
    proverb = random.choice(proverbs)
    return proverb


def discord_context_awareness(args: Dict[str, Any]) -> Dict[str, Any]:
    """Provide Discord context-aware responses."""
    context_type = args.get("context_type", "general")
    
    contexts = {
        "gaming": [
            "Yo game kya hai bro? PUBG hola?",
            "Gaming garne time ho, rank k cha?",
            "Game strong! Ko rank cha hai?",
            "Match garu? Team banaun?"
        ],
        "study": [
            "Padhai kaise chal raha hai?",
            "Exam kahan hai bhai? Prep gar na",
            "Books open gar, future banau",
            "Studies focus gar, baaki baad ma"
        ],
        "music": [
            "K gaune cha? Nepali songs?",
            "Music sunnu ma Ramro cha!",
            "Playlist banaunu?",
            "K songs sunne ho recommend?"
        ],
        "tech": [
            "K tech setup cha? PC mobile?",
            "Coding gardai cha?",
            "New gadgets k cha?",
            "Tech talk garnu hos!"
        ],
        "general": [
            "K cha khabar?",
            "Ke garne bhai?",
            "K thyo aile?",
            "Normal chat garnu hai"
        ]
    }
    
    responses = contexts.get(context_type, contexts["general"])
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
    
    translations = {
        "hello": "Namaste",
        "how are you": "K cha hajur?",
        "i am fine": "Ma thik chu",
        "what are you doing": "Ke garne hai?",
        "where are you going": "kahan jaane hai?",
        "come here": "yeta aau",
        "go there": "tyeta jaau",
        "eat food": "khaana kha",
        "drink water": "pani piu",
        "good morning": "suwa prabhat",
        "good night": "suwa ratri",
        "thank you": "dhanyabaad",
        "welcome": "swagatam",
        "sorry": "maaf garnu",
        "yes": "haan",
        "no": "haina",
        "okay": "thik cha",
        "bye": "bye",
        "see you later": "pachi bhetaunla",
        "good luck": "shubh kamana",
        "congratulations": "badhai cha"
    }
    
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