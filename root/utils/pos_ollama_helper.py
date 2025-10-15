import subprocess

OLLAMA_PATH = "/usr/local/bin/ollama"

def clean_output(output: str) -> str:
    """Basic cleanup for Ollama output."""
    return output.strip()

import json
import subprocess

def query_ollama(question, context=""):
    prompt = (
        "You are Silverline's intelligent assistant. Only respond with JSON.\n"
        "Extract the user's intent and, if they are ordering something, identify ALL products and their respective quantities.\n"
        "Respond STRICTLY in JSON format like this:\n"
        "{\n"
        '  "intent": "show_products" | "show_categories" | "place_order" | "confirm_order" | "cancel_order" | "product_question" | "small_talk" | "none",\n'
        '  "category_filter": <category name or null>,\n'
        '  "order_items": [ {"product": "<product_name>", "quantity": <number>} ... ],\n'
        '  "negative_intent": true | false\n'
        "}\n\n"
        f"Context: {context}\n"
        f"Question: {question}\n"
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
        return output
    except Exception as e:
        print("[Ollama Error]", e)
        return json.dumps({
            "intent": "none",
            "category_filter": None,
            "order_items": [],  # ✅ return empty array instead of single product
            "negative_intent": False
        })


