
import json
import subprocess
import redis


OLLAMA_PATH = "/usr/local/bin/ollama"

def clean_output(output: str) -> str:
    """Basic cleanup for Ollama output."""
    return output.strip()

from .redis_func import get_conversation_context_with_intent
# def query_ollama(question, context=""):
    

import subprocess
import json

OLLAMA_PATH = "/usr/local/bin/ollama"  # change path if needed

def clean_output(text: str):
    """Cleans model output to ensure valid JSON."""
    text = text.strip()
    if text.startswith("```json"):
        text = text.replace("```json", "").replace("```", "")
    elif text.startswith("```"):
        text = text.replace("```", "")
    # Sometimes model outputs extra explanation — remove after JSON
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return text[start:end]
    except Exception:
        return text


import json
import subprocess
import redis


OLLAMA_PATH = "/usr/local/bin/ollama"

def clean_output(output: str) -> str:
    """Basic cleanup for Ollama output."""
    return output.strip()

from .redis_func import get_conversation_context_with_intent
# def query_ollama(question, context=""):

#     prompt = (
#         "You are Silverline's intelligent assistant. Only respond in JSON.\n"
#         "Extract user's intent: show_products, show_categories, place_order, confirm_order, cancel_order, product_question, small_talk, add_attribute, none.\n"
#         "If user is ordering, identify ALL products and their quantities.\n"
#         "For each product, extract variants: color, size, gender, style, season, fit.\n"
#         "If any info is missing for an item, include it in a list 'missing_slots'.\n"
#         "For 'confirm_order', extract customer_details: name, address, phone.\n"
#         "Strict JSON format:\n"
#         "{\n"
#         '  "intent": "place_order" | "show_products" | "show_categories" | "confirm_order" | "cancel_order" | "product_question" | "small_talk" | "none",\n'
#         '  "category_filter": <category name or null>,\n'
#         '  "order_items": [\n'
#         '    {\n'
#         '      "product": "<product_name>",\n'
#         '      "quantity": <number or null>,\n'
#         '      "color": "<color or null>",\n'
#         '      "size": "<size or null>",\n'
#         '      "gender": "<gender or null>",\n'
#         '      "style": "<style or null>",\n'
#         '      "season": "<season or null>",\n'
#         '      "fit": "<fit or null>",\n'
#         '      "missing_slots": ["quantity", "size"]\n'
#         '    } ...\n'
#         '  ],\n'
#         '  "customer_details": {"name": "<name or null>", "address": "<address or null>", "phone": "<phone or null>"},\n'
#         '  "negative_intent": true | false\n'
#         "}\n"
#         f"Context: {context}\n"
#         f"Question: {question}\n"
#         "Answer (JSON only):"
#     )
#     try:
#         result = subprocess.run(
#             [OLLAMA_PATH, "run", "llama3.2:3b"],
#             input=prompt.encode("utf-8"),
#             capture_output=True,
#             timeout=60
#         )
#         raw_output = result.stdout.decode("utf-8")
#         output = clean_output(raw_output)
#         return output
#     except Exception as e:
#         print("[Ollama Error]", e)
#         return json.dumps({
#             "intent": "none",
#             "category_filter": None,
#             "order_items": [],
#             "customer_details": {"name": None, "address": None, "phone": None, "missing_customer_slots": []},
#             "negative_intent": False
#         })
    

import subprocess
import json

OLLAMA_PATH = "/usr/local/bin/ollama"  # change path if needed

def clean_output(text: str):
    """Cleans model output to ensure valid JSON."""
    text = text.strip()
    if text.startswith("```json"):
        text = text.replace("```json", "").replace("```", "")
    elif text.startswith("```"):
        text = text.replace("```", "")
    # Sometimes model outputs extra explanation — remove after JSON
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return text[start:end]
    except Exception:
        return text

def query_ollama(question, context=""):
    """
    Uses Ollama (LLaMA3) to extract structured intents and entities from user messages.
    Handles: place_order, confirm_order, add_attribute, etc.
    """

    prompt = (
        "You are Silverline's intelligent order assistant. Respond in STRICT JSON only — no text outside JSON.\n"
        "Your job: understand customer messages and extract structured data for an ordering chatbot.\n\n"
        "INTENTS:\n"
        "1. show_products → user asks to see products.\n"
        "2. show_categories → user asks for category listing.\n"
        "3. place_order → user orders one or more items.\n"
        "4. confirm_order → user confirms with name, address, and phone.\n"
        "5. cancel_order → user cancels an order.\n"
        "6. product_question → user asks details about a product.\n"
        "7. small_talk → greetings or unrelated messages.\n"
        "8. add_attribute → user specifies missing attributes (like size/color) after being asked.\n"
        "9. none → when unclear.\n\n"

        "ORDER EXTRACTION RULES:\n"
        "- Treat any user message that mentions a product in the context of wanting it as a 'place_order' intent, even if no explicit word 'order' or quantity is mentioned.\n"
        "- Examples:\n"
        "  - 'I want momo' → {'product': 'momo', 'quantity': 1}\n"
        "  - 'I want to order pizza' → {'product': 'pizza', 'quantity': 1}\n"
        "  - 'I want 2 pizzas' → {'product': 'pizza', 'quantity': 2}\n"
        "  - 'Can I have one momo' → {'product': 'momo', 'quantity': 1}\n"
        "- When user says 'I want 2 pizzas and 1 momo', extract both as separate order_items.\n"
        "- When user says 'onion rings 2', treat as product='onion rings', quantity=2.\n"
        "- When user only says something like 'L Golden', 'l golden', 's red', 'm white', 'M White', 'xl Blue', or 'xxxl green', treat this as intent='add_attribute'."
        "- Split the message into size and color:"
        "- - The first token (like s, m, l, xl, xxl, etc.) is ALWAYS the *size* if it matches these common clothing sizes."
        "- The rest of the text (like 'red', 'golden', 'blue') is the *color*."
        "- Example mappings:"
            "'M red' → size='m', color='red'"
            "'L Blue' → size='l', color='blue'"
            "'xl golden' → size='xl', color='golden'"
            "'xxl white' → size='xxl', color='white'"
        "- Extract any variants: color, size, gender, style, season, fit.\n"
        "- If user misses any details (like size or quantity), include them in 'missing_slots'.\n"
        "- All text fields should be lowercase (except names or addresses in confirm_order).\n"
        "- For 'confirm_order', extract name, address, phone from message.\n"
        "- Always ensure valid JSON output — no explanations or text outside the JSON.\n\n"

        "STRICT JSON FORMAT:\n"
        "{\n"
        '  "intent": "place_order" | "show_products" | "show_categories" | "confirm_order" | "cancel_order" | "product_question" | "small_talk" | "add_attribute" | "none",\n'
        '  "category_filter": "<category or null>",\n'
        '  "order_items": [\n'
        '    {\n'
        '      "product": "<product_name or null>",\n'
        '      "quantity": <number or null>,\n'
        '      "color": "<color or null>",\n'
        '      "size": "<size or null>",\n'
        '      "gender": "<gender or null>",\n'
        '      "style": "<style or null>",\n'
        '      "season": "<season or null>",\n'
        '      "fit": "<fit or null>",\n'
        '      "missing_slots": ["quantity", "size", ...]\n'
        '    }\n'
        '  ],\n'
        '  "customer_details": {\n'
        '      "name": "<name or null>",\n'
        '      "address": "<address or null>",\n'
        '      "phone": "<phone or null>"\n'
        '  },\n'
        '  "negative_intent": true | false\n'
        "}\n\n"
        f"Context: {context}\n"
        f"User message: {question}\n"
        "Output (JSON only):"
        "Make sure your output ends with a closing curly brace '}' and nothing else."
    )

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=60
        )

        raw_output = result.stdout.decode("utf-8").strip()
        output_str = clean_output(raw_output)

        try:
            parsed = json.loads(output_str)
        except json.JSONDecodeError:
            print("[⚠️ Ollama JSON Decode Error] Raw output:", raw_output)
            return {
                "intent": "none",
                "category_filter": None,
                "order_items": [],
                "customer_details": {
                    "name": None, "address": None, "phone": None
                },
                "negative_intent": False
            }

        return parsed

    except subprocess.TimeoutExpired:
        print("[⏱️ Ollama Timeout]")
    except Exception as e:
        print("[Ollama Error]", e)

    # fallback safe JSON
    return {
        "intent": "none",
        "category_filter": None,
        "order_items": [],
        "customer_details": {"name": None, "address": None, "phone": None},
        "negative_intent": False
    }

def enhanced_query_ollama(question, context=""):
    """Enhanced Ollama query that properly extracts quantities and products from natural language"""
    
    prompt = (
        "You are an order processing assistant. Extract order information from user messages.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. When user says 'momo 1' or 'pizza 2', extract product as 'momo' and quantity as 1\n"
        "2. When user says '1 momo and 2 pizzas', extract BOTH items\n"
        "3. Understand that 'momo 1' means 1 momo, not product 'momo 1'\n"
        "4. Always look for quantity numbers that come before OR after product names\n"
        "5. Handle singular/plural: 'pizza' = 1 pizza, 'pizzas' = quantity depends on context\n"

        "\n"
        "EXAMPLES:\n"
        "User: 'momo 1' → {'product': 'momo', 'quantity': 1}\n"
        "User: '1 momo' → {'product': 'momo', 'quantity': 1}\n"  
        "User: 'pizza 2' → {'product': 'pizza', 'quantity': 2}\n"
        "User: '2 pizza' → {'product': 'pizza', 'quantity': 2}\n"
        "User: 'momo 1 and pizza 2' → [{'product': 'momo', 'quantity': 1}, {'product': 'pizza', 'quantity': 2}]\n"
        "User: '1 momo, 2 pizzas' → [{'product': 'momo', 'quantity': 1}, {'product': 'pizza', 'quantity': 2}]\n"
        "User: 'I want momo' → {'product': 'momo', 'quantity': null}\n"
        "User: 'order pizza' → {'product': 'pizza', 'quantity': null}\n"
        "\n"
        "Respond in STRICT JSON format only:\n"
        "{\n"
        '  "intent": "place_order" | "show_products" | "show_categories" | "confirm_order" | "cancel_order" | "product_question" | "small_talk" | "none",\n'
        '  "category_filter": "<category name or null>",\n'
        '  "order_items": [\n'
        '    {\n'
        '      "product": "<extracted product name>",\n'
        '      "quantity": <extracted number or null>,\n'
        '      "color": "<color or null>",\n'
        '      "size": "<size or null>",\n'
        '      "gender": "<gender or null>",\n'
        '      "style": "<style or null>",\n'
        '      "season": "<season or null>",\n'
        '      "fit": "<fit or null>",\n'
        '      "missing_slots": ["list of missing fields or empty list"]\n'
        '    }\n'
        '  ],\n'
        '  "customer_details": {"name": "<name or null>", "address": "<address or null>", "phone": "<phone or null>"},\n'
        '  "negative_intent": false\n'
        "}\n"
        "\n"
        "IMPORTANT: For 'momo 1', product should be 'momo' and quantity should be 1, NOT product 'momo 1'\n"
        "IMPORTANT: Look for numbers anywhere around the product name\n"
        "\n"
        f"User message: {question}\n"
        f"Conversation context: {context}\n"
        "\n"
        "JSON Response:"
    )
    
    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=60
        )
        raw_output = result.stdout.decode("utf-8")
        output = clean_output(raw_output)
        return output
    except Exception as e:
        print("[Ollama Error]", e)
        return json.dumps({
            "intent": "none",
            "category_filter": None,
            "order_items": [],
            "customer_details": {"name": None, "address": None, "phone": None},
            "negative_intent": False
        })
    
def query_ollama_with_context(question, sender_id):
    """Enhanced Ollama query that uses Redis conversation context"""
    
    conversation_context = get_conversation_context_with_intent(sender_id)


    # # Get pending product for quantity if any
    # order_state_json = redis.get(f"order_state:{sender_id}")
    # pending_product = None
    # if order_state_json:
    #     state_data = json.loads(order_state_json)
    #     if state_data.get("state") == "awaiting_quantity":
    #         pending_product = state_data.get("awaiting_for")

    # # Include in context so LLM knows
    # if pending_product:
    #     conversation_context += f"\nPending product awaiting quantity: {pending_product}"
    
    prompt = (
        "You are Silverline's intelligent assistant. Only respond in JSON.\n"
        "CRITICAL RULES:\n"
        "1. NEVER invent product names. Only use products mentioned in the current or previous messages.\n"
        "2. When user responds to quantity questions, maintain the SAME product from previous context.\n"
        "3. If previous context mentions a product, use THAT product name, don't invent new ones.\n"
        "\n"
        "CONVERSATION HISTORY:\n"
        f"{conversation_context}\n"
        "\n"
        "EXAMPLES:\n"
        "Previous: User ordered 'burger', Bot asked for quantity\n"
        "Current: User says 'two' → {'product': 'burger', 'quantity': 2}\n"
        "\n"
        "Previous: User ordered 'pizza', Bot asked for quantity  \n"
        "Current: User says '3' → {'product': 'pizza', 'quantity': 3}\n"
        "\n"
        "Extract user's intent: show_products, show_categories, place_order, confirm_order, cancel_order, product_question, small_talk, none.\n"
        "If user is ordering, identify ALL products and their quantities.\n"
        "For each product, extract variants: color, size, gender, style, season, fit.\n"
        "If any info is missing for an item, include it in a list 'missing_slots'.\n"
        "For 'confirm_order', extract customer_details: name, address, phone.\n"
        "Strict JSON format:\n"
        "{\n"
        '  "intent": "place_order" | "show_products" | "show_categories" | "confirm_order" | "cancel_order" | "product_question" | "small_talk" | "none",\n'
        '  "category_filter": <category name or null>,\n'
        '  "order_items": [\n'
        '    {\n'
        '      "product": "<product_name>",\n'
        '      "quantity": <number or null>,\n'
        '      "color": "<color or null>",\n'
        '      "size": "<size or null>",\n'
        '      "gender": "<gender or null>",\n'
        '      "style": "<style or null>",\n'
        '      "season": "<season or null>",\n'
        '      "fit": "<fit or null>",\n'
        '      "missing_slots": ["quantity", "size"]\n'
        '    } ...\n'
        '  ],\n'
        '  "customer_details": {"name": "<name or null>", "address": "<address or null>", "phone": "<phone or null>"},\n'
        '  "negative_intent": true | false\n'
        "}\n"
        f"Current user message: {question}\n"
        "Answer (JSON only):"
    )
    
    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=60
        )
        raw_output = result.stdout.decode("utf-8")
        output = clean_output(raw_output)
        print(f"🔍 LLM PROMPT CONTEXT: {conversation_context}")
        print(f"📨 LLM RAW RESPONSE: {raw_output}")
        return output
    except Exception as e:
        print("[Ollama Error]", e)
        return json.dumps({
            "intent": "none",
            "category_filter": None,
            "order_items": [],
            "customer_details": {"name": None, "address": None, "phone": None},
            "negative_intent": False
        })


