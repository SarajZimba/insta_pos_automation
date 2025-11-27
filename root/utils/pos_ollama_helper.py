
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

import subprocess
import json

OLLAMA_PATH = "/usr/local/bin/ollama"  # change path if needed

# def clean_output(text: str):
#     """Cleans model output to ensure valid JSON."""
#     text = text.strip()
#     if text.startswith("```json"):
#         text = text.replace("```json", "").replace("```", "")
#     elif text.startswith("```"):
#         text = text.replace("```", "")
#     # Sometimes model outputs extra explanation — remove after JSON
#     try:
#         start = text.index("{")
#         end = text.rindex("}") + 1
#         return text[start:end]
#     except Exception:
#         return text

# def query_ollama(question, context=""):
#     """
#     Uses Ollama (LLaMA3) to extract structured intents and entities from user messages.
#     Handles: place_order, confirm_order, add_attribute, etc.
#     """

#     prompt = (
#         "You are an intent classifier for an e-commerce chatbot. Respond in STRICT JSON only — no text outside JSON.\n"
#         "Your job: understand customer latest messages and extract structured data for an ordering chatbot.\n\n"
#         "INTENTS:\n"
#         "1. show_products → user asks to see products.\n"
#         "2. show_categories → user asks for category listing.\n"
#         "3. place_order → user orders one or more items.\n"
#         "4. confirm_order → user confirms with name, address, and phone.\n"
#         "5. cancel_order → user cancels an order.\n"
#         "6. product_question → user asks details about a product.\n"
#         "7. small_talk → greetings or unrelated messages.\n"
#         "8. add_attribute → user specifies missing attributes (like size/color) after being asked.\n"
#         "9. none → when unclear.\n\n"
#         "10. place_quantity → user specifies quantities for products after being asked."
#         "11. check_out → user wants to checkout his order"
#         "12. view_cart → user wants to view his cart"

#         "🚨 CRITICAL RULE (OVERRIDES EVERYTHING ELSE):\n"
#         "If the user message is ONLY a number OR a spelled-out number,"
#         "AND no product name is mentioned, ALWAYS classify as:\n"
#         "{'intent': 'place_quantity','order_items': [{ 'product': null, 'quantity': <converted_number>, 'missing_slots': [] }]}\n"
#         "Do NOT classify such messages as show_products, show_categories, or anything else.\n"
#         "This rule must always take priority.\n\n"
#         "ORDER EXTRACTION RULES:\n"
#         "Never assume quantity is 1 by default"
#         "- Treat any user message that mentions a product in the context of wanting it as a 'place_order' intent, even if no explicit word 'order' or quantity is mentioned.\n"
#         "- Examples:\n"
#         "  -'I want this hat' → product='hat', quantity=0'\n"
#         "  - 'I want momo' → {'product': 'momo', 'quantity': 0}\n"
#         "  - 'I want to order pizza' → {'product': 'pizza', 'quantity': 0}\n"
#         "  - 'I want 2 pizzas' → {'product': 'pizza', 'quantity': 2}\n"
#         "  - 'Can I have one momo' → {'product': 'momo', 'quantity': 1}\n"
#         "- When user says 'I want 2 pizzas and 1 momo', extract both as separate order_items.\n"
#         "- When user says 'onion rings 2', treat as product='onion rings', quantity=2.\n"
#         "- When user only says something like 'L Golden', 'l golden', 's red', 'm white', 'M White', 'xl Blue', or 'xxxl green', treat this as intent='add_attribute'."
#         "- Split the message into size and color:"
#         "- - The first token (like s, m, l, xl, xxl, etc.) is ALWAYS the *size* if it matches these common clothing sizes."
#         "- The rest of the text (like 'red', 'golden', 'blue') is the *color*."
#         "- Example mappings:"
#             "'M red' → size='m', color='red'"
#             "'L Blue' → size='l', color='blue'"
#             "'xl golden' → size='xl', color='golden'"
#             "'xxl white' → size='xxl', color='white'"
#         "- Extract any variants: color, size, gender, style, season, fit.\n"
#         "- If user misses any details (like size or quantity), include them in 'missing_slots'.\n"
#         "- All text fields should be lowercase (except names or addresses in confirm_order).\n"
#         "- Treat any message that contains only a number (like '1', '2', '3') or a number spelled out ('one', 'two', 'three') and no product name as intent='place_quantity'."
#         "- Examples:"
#         "'1' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 1, ...}]}"
#         "'2' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 2, ...}]}"
#         "'3' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 3, ...}]}"
#         "'two' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 2, ...}]}"
#         "'three' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 3, ...}]}"
#         "- If the message contains a product name along with a number, treat it as 'place_order'."
#         "- For 'confirm_order', extract name, address, phone from message.\n"
#         "- Always ensure valid JSON output — no explanations or text outside the JSON.\n\n"

#         "CHECK_OUT INTENT RULES:\n"
#         "- When the user says anything indicating they want to proceed with their cart or finalize their purchase, classify as 'check_out' intent.\n"
#         "- Example phrases:\n"
#         "  - 'I want to checkout'\n"
#         "  - 'Proceed to checkout'\n"
#         "  - 'I’m ready to pay'\n"
#         "  - 'Proceed to payment'\n"
#         "  - 'Complete my order'\n"
#         "  - 'Finalize my order'\n"
#         "  - 'Let’s finish this order'\n"
#         "- Do not confuse with 'confirm_order' — confirm_order is when the user gives their name, address, or phone.\n"
#         "- check_out is when the user simply expresses intent to proceed with existing cart/order items before giving any details.\n"


#         "VIEW_CART INTENT RULES:\n"
#         "- When the user says anything indicating they want to view with their cart classify as 'view_cart' intent.\n"
#         "- Example phrases:\n"
#         "  - 'I want to view cart'\n"
#         "  - 'I’m ready to see cart'\n"
#         "  - 'Show me cart items'\n"
#         "  - 'Let’s see cart items'\n"
#         "- view_cart is when the user simply expresses intent to view existing cart/order items.\n"


#         "STRICT JSON FORMAT:\n"
#         "{\n"
#         '  "intent": "place_order" | "show_products" | "show_categories" | "confirm_order" | "cancel_order" | "product_question" | "small_talk" | "place_quantity" | "add_attribute" | "none",\n'
#         '  "category_filter": "<category or null>",\n'
#         '  "order_items": [\n'
#         '    {\n'
#         '      "product": "<product_name or null>",\n'
#         '      "quantity": <number or null>,\n'
#         '      "color": "<color or null>",\n'
#         '      "size": "<size or null>",\n'
#         '      "gender": "<gender or null>",\n'
#         '      "style": "<style or null>",\n'
#         '      "season": "<season or null>",\n'
#         '      "fit": "<fit or null>",\n'
#         '      "missing_slots": ["quantity", "size", ...]\n'
#         '    }\n'
#         '  ],\n'
#         '  "customer_details": {\n'
#         '      "name": "<name or null>",\n'
#         '      "address": "<address or null>",\n'
#         '      "phone": "<phone or null>"\n'
#         '  },\n'
#         '  "negative_intent": true | false\n'
#         "}\n\n"
#         f"Context: {context}\n"
#         f"User message: {question}\n"
#         "Output (JSON only):"
#         "NEVER assume that a number like '1', '2', '3' refers to categories or product list selections."
#         "Always interpret number-only messages as quantity input."
#         "Make sure your output ends with a closing curly brace '}' and nothing else."
#     )

#     try:
#         result = subprocess.run(
#             [OLLAMA_PATH, "run", "llama3.2:3b"],
#             input=prompt.encode("utf-8"),
#             capture_output=True,
#             timeout=60
#         )

#         raw_output = result.stdout.decode("utf-8").strip()
#         output_str = clean_output(raw_output)

#         try:
#             parsed = json.loads(output_str)
#         except json.JSONDecodeError:
#             print("[⚠️ Ollama JSON Decode Error] Raw output:", raw_output)
#             return {
#                 "intent": "none",
#                 "category_filter": None,
#                 "order_items": [],
#                 "customer_details": {
#                     "name": None, "address": None, "phone": None
#                 },
#                 "negative_intent": False
#             }

#         return parsed

#     except subprocess.TimeoutExpired:
#         print("[⏱️ Ollama Timeout]")
#     except Exception as e:
#         print("[Ollama Error]", e)

#     # fallback safe JSON
#     return {
#         "intent": "none",
#         "category_filter": None,
#         "order_items": [],
#         "customer_details": {"name": None, "address": None, "phone": None},
#         "negative_intent": False
#     }

# def clean_output(text: str):
#     """Extract only the valid JSON block from the LLaMA output."""

#     import re
#     import json

#     # Remove code fences if present
#     text = text.strip()
#     text = text.replace("```json", "").replace("```", "")

#     # Find all possible { ... } blocks
#     candidates = re.findall(r"\{.*?\}", text, re.DOTALL)

#     if not candidates:
#         return text  # Nothing to clean

#     # Try each candidate; return the first valid JSON
#     for block in candidates:
#         try:
#             json.loads(block)
#             return block  # return perfect block
#         except:
#             continue

#     # If none are valid, return the entire cleaned text
#     return text

def clean_output(text: str):
    """Clean and extract the most valid JSON from Ollama output."""
    import re
    import json

    text = text.strip()
    text = text.replace("```json", "").replace("```", "").strip()

    # Extract everything between the FIRST '{' and LAST '}'
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        json_candidate = text[start:end]

        # 🔥 FIX EXTRA TRAILING BRACES (common Ollama issue)
        while json_candidate.endswith("}}") and not json_candidate.endswith("}}\""):
            # Keep removing trailing brace until valid
            try:
                json.loads(json_candidate)
                break
            except:
                json_candidate = json_candidate[:-1]

        # Try loading
        json.loads(json_candidate)
        return json_candidate

    except Exception:
        pass

    # Fallback: try all block matches
    candidates = re.findall(r"\{.*?\}", text, re.DOTALL)
    for block in candidates:
        try:
            json.loads(block)
            return block
        except:
            continue

    return text



def query_ollama(question, context="", product_titles=None):
    """
    Uses Ollama (LLaMA3) to extract structured intents and entities from user messages.
    Handles: place_order, confirm_order, add_attribute, etc.
    """

    prompt = (
        "You are an intent classifier for an e-commerce chatbot. Respond in STRICT JSON only — no text outside JSON.\n"
        "Your job: understand customer latest messages and extract structured data for an ordering chatbot.\n\n"

        "AVAILABLE_PRODUCTS:\n"
        f"{product_titles}\n"
        "RULE: If a user refers to a product that is similar to or close to any product name here, "
        "use the closest matching product name from AVAILABLE_PRODUCTS.\n"
        "If no clear match, keep product=null.\n\n"


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
        "10. place_quantity → user specifies quantities for products after being asked."
        "11. check_out → user wants to checkout his order"
        "12. view_cart → user wants to view his cart"
        "13. view_clearance_sales → user wants to see clearance_sales"

        "🚨 CRITICAL RULE (OVERRIDES EVERYTHING ELSE):\n"
        "If the user message is ONLY a number OR a spelled-out number,"
        "AND no product name is mentioned, ALWAYS classify as:\n"
        "{'intent': 'place_quantity','order_items': [{ 'product': null, 'quantity': <converted_number>, 'missing_slots': [] }]}\n"
        "Do NOT classify such messages as show_products, show_categories, or anything else.\n"
        "This rule must always take priority.\n\n"
        "ORDER EXTRACTION RULES:\n"
        "Never assume quantity is 1 by default"
        "- Treat any user message that mentions a product in the context of wanting it as a 'place_order' intent, even if no explicit word 'order' or quantity is mentioned.\n"
        "- Examples:\n"
        "  -'I want this hat' → product='hat', quantity=0'\n"
        "  - 'I want momo' → {'product': 'momo', 'quantity': 0}\n"
        "  - 'I want to order pizza' → {'product': 'pizza', 'quantity': 0}\n"
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
        "- Treat any message that contains only a number (like '1', '2', '3') or a number spelled out ('one', 'two', 'three') and no product name as intent='place_quantity'."
        "- Examples:"
        "'1' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 1, ...}]}"
        "'2' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 2, ...}]}"
        "'3' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 3, ...}]}"
        "'two' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 2, ...}]}"
        "'three' → {'intent': 'place_quantity', 'order_items': [{'product': null, 'quantity': 3, ...}]}"
        "- If the message contains a product name along with a number, treat it as 'place_order'."
        "- For 'confirm_order', extract name, address, phone from message.\n"
        "- Always ensure valid JSON output — no explanations or text outside the JSON.\n\n"

        "CHECK_OUT INTENT RULES:\n"
        "- When the user says anything indicating they want to proceed with their cart or finalize their purchase, classify as 'check_out' intent.\n"
        "- Example phrases:\n"
        "  - 'I want to checkout'\n"
        "  - 'Proceed to checkout'\n"
        "  - 'I’m ready to pay'\n"
        "  - 'Proceed to payment'\n"
        "  - 'Complete my order'\n"
        "  - 'Finalize my order'\n"
        "  - 'Let’s finish this order'\n"
        "- Do not confuse with 'confirm_order' — confirm_order is when the user gives their name, address, or phone.\n"
        "- check_out is when the user simply expresses intent to proceed with existing cart/order items before giving any details.\n"


        "VIEW_CART INTENT RULES:\n"
        "- When the user says anything indicating they want to view with their cart classify as 'view_cart' intent.\n"
        "- Example phrases:\n"
        "  - 'I want to view cart'\n"
        "  - 'I’m ready to see cart'\n"
        "  - 'Show me cart items'\n"
        "  - 'Let’s see cart items'\n"
        "- view_cart is when the user simply expresses intent to view existing cart/order items.\n"

        "VIEW_CLEARANCE_SALES INTENT RULES:\n"
        "- When the user says anything indicating they want to view clearance sales or promotional prodcuts classify as 'view_clearance_sales' intent.\n"
        "- Example phrases:\n"
        "  - 'I want to see clearance sales'\n"
        "  - 'Show me clearance sales'\n"
        "  - 'Show me promotional products'\n"
        "  - 'Let’s see clearance sales'\n"
        "  - 'clearance sales'\n"
        "  - 'promotional_products'\n"
        "- view_clearance_sales is when the user simply expresses intent to view clearance sales/promotional products.\n"


        "STRICT JSON FORMAT:\n"
        "{\n"
        '  "intent": "place_order" | "show_products" | "show_categories" | "confirm_order" | "cancel_order" | "product_question" | "small_talk" | "place_quantity" | "add_attribute" | "none",\n'
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
        "NEVER assume that a number like '1', '2', '3' refers to categories or product list selections."
        "Always interpret number-only messages as quantity input."
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


def query_ollama_confirmation(user_message):
    """
    Uses LLaMA to determine if user is confirming (yes) or rejecting (no).
    Returns:
        "confirm_yes" | "confirm_no" | "unknown"
    """
    prompt = f"""
You are a chatbot assistant that interprets short user responses for confirmation.

Instructions:
- Only determine if the user is saying YES or NO.
- Consider variations like "yes", "yeah", "yess please", "ok", "sure", "yup", "yes confirm", "confirm" etc. as YES.
- Consider variations like "no", "nah", "nahi", "cancel", "nope", "dont confirm yet" etc. as NO.
- If it's unclear, return "unknown".
- Respond in strict JSON ONLY with one key "intent" and value "confirm_yes", "confirm_no", or "unknown".

User message: "{user_message}"

JSON Response:
{{ "intent": "<your_value_here>" }}
    """

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )
        raw_output = result.stdout.decode("utf-8").strip()
        # optional: clean output function
        output_str = raw_output.split("\n")[-1]  # take last line assuming JSON
        parsed = json.loads(output_str)
        return parsed.get("intent", "unknown")
    except Exception as e:
        print("[Ollama Error]", e)
        return "unknown"
    
def query_ollama_confirmation_order(user_message):
    """
    Uses LLaMA to determine if user is confirming (yes/order) or rejecting (no).
    Returns:
        "confirm_yes" | "confirm_no" | "unknown"
    """
    prompt = f"""
You are a chatbot assistant that interprets short user responses for confirmation.

TASK:
Classify the user's message ONLY into one of these:
- "confirm_yes" → user is confirming or wants to proceed with the order
- "confirm_no" → user rejects, declines, cancels, or wants to stop
- "unknown" → cannot determine

YES examples (return "confirm_yes"):
- yes
- yeah
- yup
- ok go ahead
- confirm
- please order
- yes I want to order
- I want to order
- I want to order this
- place the order
- yes please proceed
- ok buy it
- order it
- I would like to order

NO examples (return "confirm_no"):
- no
- nah
- nahi
- stop
- cancel
- don't order
- not now
- leave it

Respond **ONLY** in JSON like:
{{ "intent": "confirm_yes" }}

User message: "{user_message}"

JSON Response:
{{ "intent": "<your_value_here>" }}
    """

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )
        raw_output = result.stdout.decode("utf-8").strip()
        output_str = raw_output.split("\n")[-1]
        parsed = json.loads(output_str)
        return parsed.get("intent", "unknown")
    except Exception as e:
        print("[Ollama Error]", e)
        return "unknown"


# import re

# def query_ollama_quantity(user_message):
#     """
#     Uses LLaMA to extract numeric quantity from user's message.
#     Returns an integer (0 if not clear).
#     """
#     prompt = f"""
# You are an intelligent parser that extracts product quantity from short user messages.

# Instructions:
# - The user may say a number in digits or words (e.g., "3", "three", "two pieces", "I want five").
# - Your job is to extract the quantity as a number.
# - If the quantity is not clear, return 0.
# - Only return a JSON object with one key "quantity" and its integer value.
# - Do NOT include any text other than JSON.

# User message: "{user_message}"

# JSON Response:
# {{ "quantity": <number> }}
# """

#     try:
#         result = subprocess.run(
#             [OLLAMA_PATH, "run", "llama3.2:3b"],
#             input=prompt.encode("utf-8"),
#             capture_output=True,
#             timeout=30
#         )
#         raw_output = result.stdout.decode("utf-8").strip()

#         # ✅ Extract JSON using regex
#         match = re.search(r'\{.*"quantity".*?\}', raw_output)
#         if not match:
#             print("[Ollama Quantity Error] Could not find JSON in output:", raw_output)
#             return 0

#         parsed = json.loads(match.group())
#         qty = parsed.get("quantity", 0)
#         return int(qty) if isinstance(qty, (int, float)) else 0

#     except Exception as e:
#         print("[Ollama Quantity Error]", e)
#         return 0

def query_ollama_quantity(user_message):
    """
    Uses LLaMA to extract numeric quantity from user's message.
    Returns an integer (0 if not clear).
    """
    import json, subprocess

    prompt = f"""
You are an extremely strict parser that extracts the numeric quantity from a user's message.

Instructions:
- ONLY return JSON, nothing else. Do NOT include explanations, greetings, or emojis.
- The JSON must contain exactly one key: "quantity", with an integer value.
- If the quantity is unclear, return 0.
- Examples:
    "I want 3" → {{ "quantity": 3 }}
    "three pieces" → {{ "quantity": 3 }}
    "I want four of these" → {{ "quantity": 4 }}
    "not sure" → {{ "quantity": 0 }}
- Respond with ONLY one line of JSON, no extra spaces or lines.

User message: "{user_message}"

Respond with ONLY JSON:
{{ "quantity": <number> }}
"""

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=60
        )
        raw_output = result.stdout.decode("utf-8").strip()

        # 🔹 Extract JSON safely (first { to last })
        start = raw_output.find("{")
        end = raw_output.rfind("}") + 1
        if start == -1 or end == -1:
            print("[Ollama Quantity Error] No JSON found in output:", raw_output)
            return 0

        json_str = raw_output[start:end]

        # 🔹 Remove surrounding quotes if any
        if (json_str.startswith('"') and json_str.endswith('"')) or \
           (json_str.startswith("'") and json_str.endswith("'")):
            json_str = json_str[1:-1]

        parsed = json.loads(json_str)
        qty = parsed.get("quantity", 0)
        return int(qty) if isinstance(qty, (int, float)) else 0

    except Exception as e:
        print("[Ollama Quantity Error]", e)
        return 0


def query_ollama_color(user_message):
    """
    Uses Ollama (LLaMA3) to extract color name from user's message.
    Examples:
        "I want green" → "green"
        "maybe blue please" → "blue"
        "the color I want is white" → "white"
        "not sure yet" → "unknown"
        "golden" → "golden"
    
    Returns:
        color (str): Extracted color name in lowercase, or "unknown" if not found.
    """

    prompt = f"""
You are a color extractor for an e-commerce chatbot.
Your task: identify the **color** the user mentioned in their message.

Guidelines:
- Return only one color name, like "red", "green", "white", etc.
- If the user says something like "I want green" or "color is blue" → return that color.
- If no clear color is mentioned → return "unknown".
- Do NOT include extra words, emojis, or punctuation.
- Respond in **strict JSON only** with the format below.

User message: "{user_message}"

JSON Response:
{{ "color": "<color or 'unknown'>" }}
    """

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )
        raw_output = result.stdout.decode("utf-8").strip()

        # Take the last JSON-looking line
        output_str = raw_output.split("\n")[-1].strip()
        parsed = json.loads(output_str)

        color = parsed.get("color", "unknown").lower().strip()
        return color
    except Exception as e:
        print("[Ollama Error - query_ollama_color]", e)
        return "unknown"


def query_ollama_size(user_message):
    """
    Extracts the size (s, m, l, xl, xxl, xxxl,xlll, small, medium, large) from user message.
    Forces LLaMA to return STRICT JSON only.
    """

    prompt = f"""
You are a size extractor for an e-commerce chatbot.

Your rules:
- Identify the size mentioned in the user's message.
- If no size is found, output: "unknown".
- You MUST respond in STRICT JSON only — no explanation, no extra text.

Examples:
"I want xlll" → "xlll"
"maybe xl please" → "xl"
"the size I want is l" → "l"

User message: "{user_message}"

Return EXACTLY this JSON format:
{{
  "size": "<size>"
}}
"""

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )

        raw_output = result.stdout.decode("utf-8").strip()

        # Because model is STRICT JSON only, last line will always be JSON.
        parsed = json.loads(raw_output)

        size = parsed.get("size", "unknown").lower().strip()
        return size

    except Exception as e:
        print("[Ollama Error - query_ollama_size]", e)
        return "unknown"

import subprocess
import json
import re

OLLAMA_PATH = "/usr/local/bin/ollama"

def query_ollama_name(user_message):
    """
    Extracts a person's name from a user message.
    Uses Ollama (LLaMA3) with examples, and falls back to regex for bare names.
    
    Returns:
        name (str): Extracted name, or "unknown" if not found.
    """

    # 1️⃣ Ollama prompt with examples, including bare names
    prompt = f"""
You are an assistant that extracts a person's full name from text.
Return only the name mentioned by the user.
If no name is found, return "unknown".
Respond in strict JSON format.

Examples:
- "My name is Ram Khadka"  → {{ "name": "Ram Khadka" }}
- "Name: Shyam Hamal"      → {{ "name": "Shyam Hamal" }}
- "I am Kiran Thapa"       → {{ "name": "Kiran Thapa" }}
- "It's John Doe"           → {{ "name": "John Doe" }}
- "Just call me Alex"       → {{ "name": "Alex" }}
- "Sita Thapa"              → {{ "name": "Sita Thapa" }}
- "Ram Khadka"              → {{ "name": "Ram Khadka" }}
- "Not sure"                → {{ "name": "unknown" }}

User message: "{user_message}"

JSON Response:
{{ "name": "<extracted name or 'unknown'>" }}
"""

    name = "unknown"

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )
        raw_output = result.stdout.decode("utf-8").strip()

        # Get last JSON-looking line
        output_str = raw_output.split("\n")[-1].strip()
        parsed = json.loads(output_str)

        name = parsed.get("name", "unknown").strip()

    except Exception as e:
        print("[Ollama Error - query_ollama_name]", e)

    # 2️⃣ Fallback regex if Ollama fails (bare name)
    if name.lower() == "unknown":
        user_message_clean = user_message.strip()
        # Accept 1-3 word names with letters only
        match = re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+){0,2}", user_message_clean)
        if match:
            name = user_message_clean

    return name


def query_ollama_phone(user_message):
    """
    Extracts a phone number from user text using Ollama (LLaMA3) with fallback to regex.
    
    Returns:
        phone (str): Extracted phone number, or "unknown" if not found.
    """

    prompt = f"""
You are an assistant that extracts a phone number from a sentence.
Return only the digits of the phone number (include country code if present).
If no phone number is found, return "unknown".
Respond in strict JSON format.

Examples:
- "My number is 9801234567"      → {{ "phone": "9801234567" }}
- "Phone: +977-9801234567"       → {{ "phone": "+9779801234567" }}
- "Call me at 980-123-4567"      → {{ "phone": "9801234567" }}
- "9801234567"                    → {{ "phone": "9801234567" }}
- "No number"                     → {{ "phone": "unknown" }}

User message: "{user_message}"

JSON Response:
{{ "phone": "<extracted phone or 'unknown'>" }}
"""

    phone = "unknown"

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )
        raw_output = result.stdout.decode("utf-8").strip()

        # Get last JSON-looking line
        output_str = raw_output.split("\n")[-1].strip()
        parsed = json.loads(output_str)

        phone = parsed.get("phone", "unknown").strip()

    except Exception as e:
        print("[Ollama Error - query_ollama_phone]", e)

    # 2️⃣ Fallback regex for phone numbers if Ollama fails
    if phone.lower() == "unknown":
        # Matches numbers like 9801234567, 980-123-4567, +9779801234567
        match = re.search(r"(\+?\d[\d\s\-]{7,14}\d)", user_message)
        if match:
            # Remove spaces and dashes
            phone = re.sub(r"[^\d+]", "", match.group(0))

    return phone


def query_ollama_address(user_message):
    """
    Extracts a real-world address/location from the user text.
    Uses Ollama first, then regex fallback.
    """

    prompt = f"""
You are an assistant that extracts the user's address or location.
Return ONLY the address/location mentioned.
If no address is found, return "unknown".

Respond in STRICT JSON ONLY.

Examples:
- "My address is Kuala Lumpur" → {{ "address": "Kuala Lumpur" }}
- "address: Baneshwor" → {{ "address": "Baneshwor" }}
- "I live in Pokhara" → {{ "address": "Pokhara" }}
- "Location is Kathmandu" → {{ "address": "Kathmandu" }}
- "Place: Lakeside Pokhara" → {{ "address": "Lakeside Pokhara" }}
- "kathmandu" → {{ "address": "Kathmandu" }}
- "Just in Lalitpur" → {{ "address": "Lalitpur" }}
- "unknown" → {{ "address": "unknown" }}

User message: "{user_message}"

JSON Response:
{{ "address": "<extracted address or 'unknown'>" }}
"""

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )

        raw_output = result.stdout.decode().strip()
        output_str = raw_output.split("\n")[-1].strip()

        parsed = json.loads(output_str)
        address = parsed.get("address", "unknown").strip()

    except Exception as e:
        print("[Ollama Error - query_ollama_address]", e)
        address = "unknown"

    # 🔥 REGEX FALLBACK (handles simple entries like “Kuala Lumpur”)
    if address.lower() == "unknown":
        msg = user_message.strip()

        # Accept 1–4 word address phrases (letters only)
        match = re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+){0,3}", msg)
        if match:
            return msg

    return address



def query_ollama_image_text_intent(user_message):
    """
    Detects user intent when an image is uploaded + text provided.
    Intents:
        - identify_product
        - ask_color_options
        - ask_size_options
        - price_query
        - order_intent
        - unknown
    """
    prompt = f"""
You are an AI assistant that determines the user's intent when they upload a product image and send a message.

Possible intents:

1. identify_product  
   When user is asking what the product is or if you sell it.
   Example:
   - "What product is this?"
   - "Do you have this?"
   - "Find similar items"
   - "Which brand is this?"

2. ask_color_options  
   When user is asking about colors.
   Example:
   - "Do you have this in blue?"
   - "What other colors are available?"
   - "Show this in red."

3. ask_size_options  
   When the user wants available sizes.
   Example:
   - "What sizes do you have?"
   - "Is this available in medium?"
   - "Small or large available?"

4. price_query  
   When the user asks about cost.
   Example:
   - "How much is this?"
   - "What's the price?"
   - "Is this on sale?"

5. order_intent  
   When user wants to buy/ add to cart / place an order.
   Example:
   - "Add this to cart"
   - "I want to order 2 pieces"
   - "Buy this in size L"
   - "I want 3 of these in blue"
   - "I want this"

If not sure, return "unknown".

Respond in STRICT JSON ONLY:
{{
  "intent": "<one_of_the_intents>"
}}

User message:
"{user_message}"
"""

    try:
        result = subprocess.run(
            [OLLAMA_PATH, "run", "llama3.2:3b"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=30
        )

        raw_output = result.stdout.decode("utf-8").strip()
        output_str = raw_output.split("\n")[-1]  # pick last line (JSON)
        parsed = json.loads(output_str)

        return parsed.get("intent", "unknown")

    except Exception as e:
        print("[Ollama Error]", e)
        return "unknown"



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


