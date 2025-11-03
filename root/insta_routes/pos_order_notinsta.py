# llama_main.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import faiss
import subprocess
from sentence_transformers import SentenceTransformer
from pypdf import PdfReader
import docx
import pandas as pd
import re
from uuid import uuid4
import time
import threading
from dotenv import load_dotenv
load_dotenv()

# from ask_menu import ask_menu
# from ask_image import ask_image

from helper_func import (
    save_document_to_db,
    load_document_from_db,
    save_image_text,
    load_image_text,
    load_document_from_db_outletwise,
    match_command,
    get_command_slots,
)

app = Flask(__name__)
CORS(app)

embedder = SentenceTransformer("all-MiniLM-L6-v2")

# Store documents per doc_id
DOCUMENTS = {}  # {doc_id: {"index": ..., "chunks": ..., "created_at": ...}}

# Auto-expiry config
EXPIRY_SECONDS = 1800  # 30 minutes

OLLAMA_PATH = "/usr/local/bin/ollama"


from file_utils import UPLOAD_FOLDER

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# ------------------------------
# Text extraction
# ------------------------------
def extract_text(file):
    """Extract text from PDF, DOCX, TXT, or Excel files."""
    if file.filename.endswith(".pdf"):
        reader = PdfReader(file)
        return " ".join([page.extract_text() or "" for page in reader.pages])
    elif file.filename.endswith(".docx"):
        doc = docx.Document(file)
        return " ".join([para.text for para in doc.paragraphs])
    elif file.filename.endswith(".txt"):
        return file.read().decode("utf-8")
    elif file.filename.endswith((".xls", ".xlsx")):
        df = pd.read_excel(file, engine="openpyxl")
        text = " ".join(df.astype(str).apply(lambda row: " ".join(row), axis=1))
        return text
    else:
        raise ValueError("Unsupported file type")


# ------------------------------
# Chunking
# ------------------------------
def chunk_text(text, chunk_size=500, overlap=50):
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunks.append(" ".join(words[i:i+chunk_size]))
    return chunks


# ------------------------------
# Build FAISS index
# ------------------------------
def build_index(chunks):
    embeddings = embedder.encode(chunks)
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings)
    return index, embeddings


# ------------------------------
# Query Llama with Hybrid RAG
# ------------------------------
def clean_output(output: str) -> str:
    """Basic cleanup for Llama output (no <think> traces like DeepSeek)."""
    return output.strip()


def query_llama(context, question, model="llama3.2:3b"):
    """Ask Llama model, preferring context but allowing outside knowledge."""
    if context.strip():
        prompt = (
            f"You are a strict assistant. Only use the provided context to answer. "
            f"If the answer is not in the context, reply exactly: "
            f"'The information is not available in the provided document.'\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {question}\n\n"
            f"Answer:"
        )
    else:
        prompt = (
            f"You are a helpful assistant. Answer the question using your own knowledge.\n\n"
            f"Question: {question}\n\n"
            f"Answer:"
        )

    result = subprocess.run(
        [OLLAMA_PATH, "run", model],
        input=prompt.encode("utf-8"),
        capture_output=True
    )
    raw_output = result.stdout.decode("utf-8")
    return clean_output(raw_output)


# ------------------------------
# Routes
# ------------------------------
@app.route("/upload", methods=["POST"])
def upload_document():
    try:
        if "file" not in request.files or "username" not in request.form:
            return jsonify({"error": "File and username are required"}), 400

        uploaded_file = request.files["file"]
        username = request.form["username"]
        document_outlet_name = request.form.get("document_outlet_name", None)

        # Extract text and chunk
        text = extract_text(uploaded_file)
        chunks = chunk_text(text)


        # Get embeddings
        embeddings = embedder.encode(chunks)

        # Save document + embeddings to DB
        doc_id = save_document_to_db(username, uploaded_file.filename, chunks, embeddings, document_outlet_name)

        return jsonify({
            "doc_id": doc_id,
            "document_outlet_name": document_outlet_name,
            "message": f"Document '{uploaded_file.filename}' loaded successfully."
        })

    except Exception as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            return jsonify({"error": error_msg}), 409  # 409 Conflict for duplicate
        return jsonify({"error": error_msg}), 500
    
from flask import jsonify

@app.route("/documents/<document_outlet_name>", methods=["GET"])
def list_documents(document_outlet_name):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Fetch all document metadata
        cursor.execute("""
            SELECT 
                id AS doc_id,
                username,
                filename,
                document_outlet_name,
                created_at
            FROM documents WHERE document_outlet_name = %s
            ORDER BY created_at DESC
        """, (document_outlet_name,))

        documents = cursor.fetchall()

        cursor.close()
        conn.close()

        # If no documents found
        if not documents:
            return jsonify({"documents": [], "message": "No documents found."}), 200

        # Return list of documents
        return jsonify({"documents": documents}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if document exists
        cursor.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
        doc = cursor.fetchone()

        if not doc:
            cursor.close()
            conn.close()
            return jsonify({"error": "Document not found"}), 404

        # Delete related embeddings first (to maintain referential integrity)
        cursor.execute("DELETE FROM embeddings WHERE document_id = %s", (doc_id,))

        # Then delete the document itself
        cursor.execute("DELETE FROM documents WHERE id = %s", (doc_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": f"Document with ID {doc_id} deleted successfully."}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
import redis
import json
import re
import datetime
from flask import Flask, request, jsonify

import redis
import datetime

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

@app.route("/ask", methods=["POST"])
def ask_question():
    try:
        data = request.get_json()
        question = data.get("question")
        doc_id = data.get("doc_id")
        document_outlet_name = data.get("document_outlet_name", None)

        if not question or not doc_id:
            return jsonify({"error": "Question and doc_id are required"}), 400

        # --- Step 1: load from DB (your code) ---
        context = ""
        try:
            chunks, index = load_document_from_db(doc_id, document_outlet_name)
            q_embed = embedder.encode([question])
            D, I = index.search(q_embed, k=3)
            context = " ".join([chunks[i] for i in I[0]])
        except Exception as e:
            print(e)
            return jsonify({"error": "Document not found or failed to load"}), 404

        # --- Step 2: Retrieve chat history ---
        session_key = f"session:{doc_id}"
        previous_msgs = r.lrange(session_key, -5, -1)
        previous_context = " ".join([
            json.loads(m)["question"] + " " + json.loads(m)["answer"]
            for m in previous_msgs
        ])

        # --- Step 3: Merge history with new context ---
        full_context = previous_context + " " + context

        # --- Step 4: Ask LLM ---
        answer = query_llama(full_context, question, model="llama3.2:3b")

        # --- Step 5: Save new QA pair to Redis ---
        message = {
            "question": question,
            "answer": answer,
            "timestamp": datetime.datetime.now().isoformat()
        }
        r.rpush(session_key, json.dumps(message))
        r.ltrim(session_key, -5, -1)  # keep last 5

        return jsonify({
            "question": question,
            "doc_id": doc_id,
            "answer": answer
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route("/ask-outlet", methods=["POST"])
def ask_question_outlet():
    try:
        data = request.get_json()
        question = data.get("question")
        document_outlet_name = data.get("document_outlet_name", None)
        if not question:
            return jsonify({"error": "Question is required"}), 400

        context = ""
        if document_outlet_name:
            try:
                chunks, index = load_document_from_db_outletwise(document_outlet_name)
                q_embed = embedder.encode([question])
                D, I = index.search(q_embed, k=3)
                context = " ".join([chunks[i] for i in I[0]])
            except Exception as e:
                print(e)
                return jsonify({"error": "Document not found or failed to load"}), 404

        # Hybrid: pass context if available, else fallback
        answer = query_llama(context, question, model="llama3.2:3b")

        return jsonify({
            "question": question,
            "document_outlet_name": document_outlet_name,
            "answer": answer
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def query_llama_with_no_slots(context, question):
    """
    Calls LLaMA with context + question. If slots are provided, tries to extract slot values.
    If slots are empty, just return answer from context.
    """

    # No slots → just answer using document context
    prompt = (
            f"You are a helpful assistant. Answer the user's question using the context.\n"
            f"Context:\n{context}\n\n"
            f"Question: {question}\n\n"
            f"If information is not available, say 'No information provided'."
        )

    output = query_llama(question + "\n" + context + "\n" + prompt, question)

    return output
    

from helper_func import get_db_connection  # assuming this exists

# ------------------------------
# Connect to Redis
# r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# ------------------------------
# Utility to get slots for a command
def get_slots_for_command(command_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT slot_id, slot_name, required
        FROM outlet_command_slots
        WHERE command_id = %s
    """, (command_id,))
    slots = cursor.fetchall()
    cursor.close()
    conn.close()
    return slots




import re
import requests

def contains_pattern(text, patterns):
    """Check if any regex pattern matches in the text."""
    text = text.lower()
    return any(re.search(pat, text) for pat in patterns)


import redis
import datetime
import json
import requests

r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

@app.route("/ask-outlet-command-slots", methods=["POST"])
def ask_outlet_command_slots():
    try:
        data = request.get_json()
        document_outlet_name = data.get("document_outlet_name")
        user_id = data.get("user_id")
        command_id = data.get("command_id")  # can be None
        user_slots = data.get("slots", {})   # optional new slot values
        question = data.get("question", "")  # user question for LLaMA

        if not document_outlet_name or not user_id:
            return jsonify({"error": "document_outlet_name and user_id are required"}), 400

        # -----------------------------------------------------------
        # 📌 1. Handle general conversation (no command_id)
        # -----------------------------------------------------------
        if not command_id and question:
            question_lower = question.lower()

            # --- Session key for conversation ---
            session_key = f"session:{document_outlet_name}:{user_id}"

            # --- Load previous 5 messages ---
            previous_msgs = r.lrange(session_key, -5, -1)
            previous_context = " ".join([
                json.loads(m)["question"] + " " + json.loads(m)["answer"]
                for m in previous_msgs
            ])

            # Normal fallback (document context + conversation)
            try:
                chunks, index = load_document_from_db_outletwise(document_outlet_name)
                context = " ".join(chunks)
                full_context = previous_context + " " + context
                llama_answer = query_llama_with_no_slots(full_context, question)
            except Exception as e:
                llama_answer = f"No document context found: {str(e)}"

            # 📝 Save message to Redis
            message = {
                "question": question,
                "answer": llama_answer,
                "timestamp": datetime.datetime.now().isoformat()
            }
            r.rpush(session_key, json.dumps(message))
            r.ltrim(session_key, -5, -1)

            return jsonify({
                "document_outlet_name": document_outlet_name,
                "command_id": None,
                "slots": {},
                "ready_to_call_api": True,
                "is_last_command": True,
                "llama_answer": llama_answer
            }), 200

        # -----------------------------------------------------------
        # 📌 2. Normal command flow (with command_id)
        # -----------------------------------------------------------
        session_key = f"{document_outlet_name}_{user_id}_{command_id}"

        session_json = r.get(session_key)
        session_slots = json.loads(session_json) if session_json else {}

        session_slots.update(user_slots)

        slots_required = get_slots_for_command(command_id)
        slots_dict = {slot["slot_name"]: session_slots.get(slot["slot_name"]) for slot in slots_required}
        ready_to_call_api = all(v is not None and v != "" for v in slots_dict.values())

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) AS count FROM outlet_commands WHERE parent_command_id = %s", (command_id,))
        subcommand_count = cursor.fetchone()["count"]
        is_last_command = subcommand_count == 0

        llama_answer = None
        if is_last_command and not slots_dict:
            cursor.execute("SELECT command_text FROM outlet_commands WHERE command_id=%s", (command_id,))
            row = cursor.fetchone()
            if row and row.get("command_text") and not question:
                question = row["command_text"]

            try:
                chunks, index = load_document_from_db_outletwise(document_outlet_name)
                context = " ".join(chunks)
                llama_answer = query_llama_with_no_slots(context, question)
            except Exception as e:
                llama_answer = f"No document context found: {str(e)}"

        cursor.close()
        conn.close()

        r.set(session_key, json.dumps(slots_dict), ex=3600)

        return jsonify({
            "document_outlet_name": document_outlet_name,
            "command_id": command_id,
            "slots": slots_dict,
            "ready_to_call_api": ready_to_call_api,
            "is_last_command": is_last_command,
            "llama_answer": llama_answer
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "POS (Llama) is running!"})


@app.route("/ask-image-question", methods=["POST"])
def ask_image_question():
    data = request.get_json()
    image_id = data.get("image_id")
    question = data.get("question")

    if not image_id or not question:
        return jsonify({"error": "image_id and question are required"}), 400

    detected_text = load_image_text(image_id)
    if not detected_text:
        return jsonify({"error": "Image not found"}), 404

    # Send detected_text as context to Llama
    answer = query_llama(detected_text, question, model="llama3.2:3b")
    return jsonify({
        "image_id": image_id,
        "question": question,
        "answer": answer
    })


def clean_output(text):
    return text.strip()


import json
import re
import datetime
import difflib
from flask import request, jsonify
import requests
import spacy  # ✅ NEW
# --- Keep your existing helpers: get_db_connection, embedder, query_llama, load_document_from_db, r (redis) ---

CATEGORIES_API_URL = "https://vibezdc.silverlinepos.com/api/categories/"
MAX_PREV_CTX = 5
FUZZY_MATCH_CUTOFF = 0.6  # 0-1 float; adjust if necessary

# ✅ Load NER model once globally
nlp_ner = spacy.load("ner_model")  # <-- replace with actual folder name

def extract_category_with_ner(text):
    """
    Returns category phrase if detected by NER model, else None.
    """
    doc = nlp_ner(text.lower())
    for ent in doc.ents:
        if ent.label_.upper() == "CATEGORY":
            return ent.text.strip()
    return None


def normalize_text(s: str) -> str:
    """Lowercase, strip, remove punctuation-like chars, and collapse spaces."""
    if not s:
        return ""
    s = s.lower()
    # keep alphanumerics and spaces
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_llama_intent(raw):
    """
    Accept raw response from LLaMA which may be:
      - a dict already
      - a JSON string
      - a plain string (fallback)
    Always return dict with keys: intent, category_filter, negative_intent
    """
    default = {"intent": "none", "category_filter": None, "negative_intent": False}
    if not raw:
        return default

    # If it's already a dict-like object, normalize keys
    if isinstance(raw, dict):
        return {
            "intent": raw.get("intent", "none"),
            "category_filter": raw.get("category_filter"),
            "negative_intent": bool(raw.get("negative_intent", False)),
        }

    # Try parsing JSON string
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {
                    "intent": parsed.get("intent", "none"),
                    "category_filter": parsed.get("category_filter"),
                    "negative_intent": bool(parsed.get("negative_intent", False)),
                }
        except Exception:
            # Not JSON, fall back to simple heuristics:
            text = raw.lower()
            if "show products" in text or "products" in text:
                return {"intent": "show_products", "category_filter": None, "negative_intent": False}
            if "categories" in text or "show categories" in text:
                return {"intent": "show_categories", "category_filter": None, "negative_intent": False}
    return default


def fetch_all_categories():
    """Safe fetch of categories API; returns list (empty on error)."""
    try:
        resp = requests.get(CATEGORIES_API_URL, timeout=6)
        if resp.status_code == 200:
            return resp.json() or []
    except Exception as e:
        print(f"[catalog] Failed to fetch categories: {e}")
    return []


def match_category_by_filter(category_filter, all_categories):
    """
    Robust matching strategy:
      1) exact normalized match
      2) startswith (normalized)
      3) whole-word match (token equality)
      4) fuzzy match on normalized titles (difflib)
    Returns matched category dict or None.
    """
    if not category_filter:
        return None

    norm_filter = normalize_text(category_filter)
    if not norm_filter:
        return None

    # Build normalized mapping
    normalized_map = []
    titles = []
    for cat in all_categories:
        title = cat.get("title", "")
        norm_title = normalize_text(title)
        normalized_map.append((norm_title, cat))
        titles.append(norm_title)

    # 1) exact
    for norm_title, cat in normalized_map:
        if norm_title == norm_filter:
            return cat

    # 2) startswith
    for norm_title, cat in normalized_map:
        if norm_title.startswith(norm_filter):
            return cat

    # 3) whole-word equality (any token equals filter)
    filter_tokens = set(norm_filter.split())
    for norm_title, cat in normalized_map:
        title_tokens = set(norm_title.split())
        if filter_tokens & title_tokens:
            # ensure we don't wrongly match 'mens' inside 'womens' — token intersection will not match unless token present
            # Example: filter "mens kurtha" tokens -> {"mens","kurtha"}; "womens kurtha" tokens -> {"womens","kurtha"}
            # Intersection will have "kurtha" but not "mens". To be conservative, require at least 1 token AND additional heuristics below.
            return cat

    # 4) fuzzy: use difflib on normalized titles
    close = difflib.get_close_matches(norm_filter, titles, n=1, cutoff=FUZZY_MATCH_CUTOFF)
    if close:
        best = close[0]
        for norm_title, cat in normalized_map:
            if norm_title == best:
                return cat

    return None


def match_product_by_filter(product_filter, all_categories):
    """
    Matches a product name across all categories.
    Returns a tuple (matched_product, parent_category) or (None, None)
    """
    if not product_filter:
        return None, None

    norm_filter = normalize_text(product_filter)
    if not norm_filter:
        return None, None

    for cat in all_categories:
        products = cat.get("products", [])
        for prod in products:
            prod_title = prod.get("title", "")
            norm_title = normalize_text(prod_title)

            # 1) exact match
            if norm_title == norm_filter:
                return prod, cat

            # 2) startswith
            if norm_title.startswith(norm_filter):
                return prod, cat

            # 3) whole-word match
            filter_tokens = set(norm_filter.split())
            title_tokens = set(norm_title.split())
            if filter_tokens & title_tokens:
                return prod, cat

    # 4) fuzzy match across all product titles
    all_product_titles = []
    product_map = {}
    for cat in all_categories:
        for prod in cat.get("products", []):
            norm_title = normalize_text(prod.get("title", ""))
            all_product_titles.append(norm_title)
            product_map[norm_title] = (prod, cat)

    close = difflib.get_close_matches(norm_filter, all_product_titles, n=1, cutoff=FUZZY_MATCH_CUTOFF)
    if close:
        best = close[0]
        return product_map.get(best)

    return None, None

def extract_excluded_products(question, categories):
    """
    Try to detect which products user wants to exclude based on the question text.
    Returns a list of product titles to remove.
    """
    excluded = []

    # Keywords indicating negative filtering
    negative_keywords = ["dont show", "don't show", "do not show", "exclude", "remove", "without"]

    q = question.lower()
    if any(kw in q for kw in negative_keywords):
        prod_match, _ = match_product_by_filter(question, categories)
        if prod_match:
            excluded.append(prod_match["title"])

    return excluded

import re

def resolve_categories(text, all_categories):
    """
    Extract categories from text using NER, handling ambiguous gender/product.
    Returns a list of matched category dicts from all_categories.
    """
    doc = nlp_ner(text)
    
    genders = []
    products = []
    exact_categories = []

    for ent in doc.ents:
        print(ent.text, ent.label_)

    # Collect entities
    for ent in doc.ents:
        if ent.label_ == "CATEGORY":
            exact_categories.append(ent.text)
        elif ent.label_ == "CATEGORY_AMBIGUOUS_PRODUCT":
            products.append(ent.text.lower())
        elif ent.label_ == "CATEGORY_AMBIGUOUS_GENDER":
            genders.append(ent.text.lower())

    matched_categories = []

    # 1️⃣ If exact categories are detected, match exactly
    if exact_categories:
        for cat in all_categories:
            cat_title = cat.get("title", "")
            if cat_title.lower() in [c.lower() for c in exact_categories]:
                matched_categories.append(cat)

    # 2️⃣ If product root only
    elif products and not genders:
        for cat in all_categories:
            cat_title = cat.get("title", "").lower()
            for prod in products:
                if re.search(rf"\b{re.escape(prod)}\b", cat_title):
                    matched_categories.append(cat)
                    break

    # 3️⃣ If gender only
    elif genders and not products:
        for cat in all_categories:
            cat_title = cat.get("title", "").lower()
            for g in genders:
                if re.search(rf"\b{re.escape(g)}\b", cat_title):
                    matched_categories.append(cat)
                    break

    # 4️⃣ If both product + gender are present
    elif products and genders:
        for cat in all_categories:
            cat_title = cat.get("title", "").lower()
            prod_match = any(re.search(rf"\b{re.escape(p)}\b", cat_title) for p in products)
            gender_match = any(re.search(rf"\b{re.escape(g)}\b", cat_title) for g in genders)
            if prod_match and gender_match:
                matched_categories.append(cat)

    return matched_categories


def generate_dynamic_response(categories, products, question):
    category_names = [c.get("title") for c in categories] if categories else []
    product_titles = [p.get("title") for cat in categories for p in cat.get("products", [])] if categories else []

    context = ""
    if category_names:
        context += "Categories available: " + ", ".join(category_names) + ".\n"
    if product_titles:
        context += "Products available: " + ", ".join(product_titles[:5]) + ".\n"

    prompt = f"""
    User asked: "{question}"
    Context:
    {context}

    Generate a friendly, natural response for the user. Mention categories or products if available.
    """
    return query_llama(context, prompt)


import spacy
from spacy.matcher import PhraseMatcher
import re

# Create a blank NLP object for PhraseMatcher
nlp_pm = spacy.blank("en")

def match_categories_phrasematcher(text, all_categories):
    """
    Fallback function to match categories using PhraseMatcher.
    Handles plurals/singulars by adding variants.
    """
    matcher = PhraseMatcher(nlp_pm.vocab, attr="LOWER")  # case-insensitive match

    patterns = []
    cat_map = {}  # Map pattern text to category dict

    for cat in all_categories:
        title = cat.get("title", "").strip()
        if not title:
            continue

        # Add original title pattern
        patterns.append(nlp_pm.make_doc(title))
        cat_map[title.lower()] = cat

        # Simple plural handling: add 's' and remove trailing 's'
        if not title.lower().endswith("s"):
            plural = title + "s"
            patterns.append(nlp_pm.make_doc(plural))
            cat_map[plural.lower()] = cat
        else:
            singular = title.rstrip("s")
            patterns.append(nlp_pm.make_doc(singular))
            cat_map[singular.lower()] = cat

    matcher.add("CATEGORY", patterns)

    doc = nlp_pm(text)
    matches = matcher(doc)
    matched_categories = []

    for match_id, start, end in matches:
        span_text = doc[start:end].text.lower()
        cat = cat_map.get(span_text)
        if cat and cat not in matched_categories:
            matched_categories.append(cat)

    return matched_categories

# from rapidfuzz import fuzz, process

# def fuzzy_category_match(text, all_categories, threshold=80):
#     """
#     Fuzzy match category titles with user text for misspellings like 'kurta' -> 'kurtha'.
#     """
#     matched = []
#     text = text.lower()

#     for cat in all_categories:
#         title = cat.get("title", "").lower()
#         similarity = fuzz.partial_ratio(text, title)  # Compare similarity
#         if similarity >= threshold:
#             matched.append(cat)

#     return matched


from rapidfuzz import fuzz

MALE_WORDS = {"men", "mens", "man", "male", "gents", "gent"}
FEMALE_WORDS = {"women", "womens", "woman", "female", "ladies", "lady"}

def fuzzy_category_match(text, all_categories, threshold=85):
    tokens = set(text.lower().split())

    # Detect gender
    gender = None
    if tokens & MALE_WORDS:
        gender = "male"
    elif tokens & FEMALE_WORDS:
        gender = "female"

    # Remove gender words for fuzzy comparison
    main_query = " ".join(tokens - MALE_WORDS - FEMALE_WORDS)

    matched = []
    for cat in all_categories:
        title_tokens = set(cat["title"].lower().split())

        # Gender filter
        if gender == "male" and not (title_tokens & MALE_WORDS):
            continue
        if gender == "female" and not (title_tokens & FEMALE_WORDS):
            continue

        # Remove gender words from category title for fuzzy match
        main_title = " ".join(title_tokens - MALE_WORDS - FEMALE_WORDS)

        # Use partial_ratio for minor spelling errors
        similarity = fuzz.partial_ratio(main_query, main_title)
        # matched = fuzzy_category_match(text, all_categories)
        if similarity >= threshold:
            matched.append(cat)

    return matched


from rapidfuzz import fuzz
import copy

def fuzzy_match_products(question: str, all_categories: list, threshold: int = 75):
    """
    Fuzzy match the question against product titles.

    Returns:
        matched_categories: list of categories containing only matched products
        matched_products: list of all matched products
    """
    question_normalized = question.lower()
    matched_products = []
    matched_categories_dict = {}

    for cat in all_categories:
        matched_prods_in_cat = []
        for prod in cat.get("products", []):
            title = prod.get("title", "").lower()
            similarity = fuzz.partial_ratio(question_normalized, title)
            if similarity >= threshold:
                matched_prods_in_cat.append(prod)
                matched_products.append(prod)

        if matched_prods_in_cat:
            # Make a copy of the category and replace its products with only matched ones
            cat_copy = copy.deepcopy(cat)
            cat_copy["products"] = matched_prods_in_cat
            matched_categories_dict[cat["id"]] = cat_copy

    matched_categories = list(matched_categories_dict.values())
    return matched_categories, matched_products



@app.route("/outlet-catalog", methods=["POST"])
def outlet_catalog():
    try:
        data = request.get_json(force=True)
        document_outlet_name = data.get("document_outlet_name")
        user_id = data.get("user_id")
        question = data.get("question", "").strip()

        if not document_outlet_name or not user_id:
            return jsonify({"error": "document_outlet_name and user_id are required"}), 400

        session_key = f"session:{document_outlet_name}:{user_id}"
        session_state_key = f"state:{document_outlet_name}:{user_id}"

        previous_msgs = r.lrange(session_key, -MAX_PREV_CTX, -1) or []
        previous_context = " ".join(
            [(json.loads(m)["question"] + " " + json.loads(m)["answer"]) for m in previous_msgs if m]
        )

        knowledge_context = ""

        # Intent detection using LLaMA
        llama_intent_prompt = (
            f"You are an assistant for a clothing outlet.\n"
            f"Return only JSON:\n"
            f"{{\"intent\": \"show_products\" | \"show_categories\" | \"none\", "
            f"\"category_filter\": <string or null>, "
            f"\"negative_intent\": true|false}}\n\n"
            f"User Question: {question}\nAnswer:"
        )
        llama_intent_raw = query_llama(previous_context + " " + knowledge_context, llama_intent_prompt)
        llama_intent = parse_llama_intent(llama_intent_raw)
        intent = llama_intent.get("intent", "none")
        negative_intent = llama_intent.get("negative_intent", False)

        # Fetch all categories from API
        all_categories = fetch_all_categories()
        question_normalized = question.lower()
        print("question normalized", question_normalized)
        # --- NER-based category detection ---
        matched_categories = resolve_categories(question_normalized, all_categories)
        print("intent", llama_intent)
        print("matched_categories", matched_categories)

        # Fallback using PhraseMatcher if NER fails
        if not matched_categories:
            matched_categories = match_categories_phrasematcher(question_normalized, all_categories)
            print("PhraseMatcher fallback matched categories:", matched_categories)

        # 3) If still empty → Fuzzy fallback (handles spelling errors)
        if not matched_categories:
            matched_categories = fuzzy_category_match(question_normalized, all_categories, threshold=85)
            print("Fuzzy fallback:", matched_categories)

        # 4) If still empty → Fuzzy match product titles
        if not matched_categories:
            matched_categories, products = fuzzy_match_products(question, all_categories, threshold=75)
            print("Fuzzy fallback on categories:", matched_categories)
            print("Fuzzy fallback on products:", products)
        # Handle negative intent early
        # if negative_intent:
        #     llama_answer = "No problem 😊 Let me know anytime."
        #     msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
        #     r.rpush(session_key, json.dumps(msg))
        #     r.ltrim(session_key, -MAX_PREV_CTX, -1)
        #     return jsonify({"llama_answer": llama_answer, "ready_to_call_api": False}), 200

        products = []
        categories = []
        llama_answer = None

        # Case: show categories
        if intent == "show_categories":
            # matched_categories = all_categories
            categories = all_categories
            for cat in matched_categories:
                products.extend(cat.get("products", []))
            llama_answer = "Here are our categories"

        # Case: show products with matched categories
        elif intent == "show_products" and matched_categories:
            products = []
            for cat in matched_categories:
                products.extend(cat.get("products", []))
            llama_answer = "Here are the products from your selected categories"
            # llama_answer = generate_dynamic_response(matched_categories, products, question)
        


        # elif intent == "show_products" and not matched_categories:
        elif matched_categories == []:
            # matched_categories = all_categories
            # for cat in all_categories:
            #     products.extend(cat.get("products", []))
            # products = None
            llama_answer = "Sorry We do not have such products"
            # llama_answer = generate_dynamic_response(all_categories, None, question)


        # elif intent == 'none':
        #     llama_answer = "Please try again later"
        # Final fallback: LLaMA answer
        if not llama_answer:
            # llama_answer = query_llama(previous_context + " " + knowledge_context, question)
            llama_answer = "Here are all our categories."

        # Save conversation
        msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
        r.rpush(session_key, json.dumps(msg))
        r.ltrim(session_key, -MAX_PREV_CTX, -1)

        return jsonify({
            "llama_answer": llama_answer,
            "categories": matched_categories or None,
            "products": products or None,
            "ready_to_call_api": bool(products or matched_categories)
        }), 200

    except Exception as e:
        print(f"[outlet_catalog ERROR] {e}")
        return jsonify({"error": str(e)}), 500




from flask import send_from_directory

# Serve uploaded images
@app.route("/uploads/commands/<filename>")
def uploaded_file(filename):
    return send_from_directory("uploads/commands", filename)



# Scheduler jobs wrapped with app context
from apscheduler.schedulers.background import BackgroundScheduler
from helper_func import delete_old_documents, delete_old_images
# ------------------------------
def scheduled_delete_documents():
    with app.app_context():
        print("[SCHEDULER] Running delete_old_documents")
        delete_old_documents()

def scheduled_delete_images():
    with app.app_context():
        print("[SCHEDULER] Running delete_old_images")
        delete_old_images()

# Start scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_delete_documents, "interval", minutes=5)
scheduler.add_job(scheduled_delete_images, "interval", minutes=5)
scheduler.start()

# Allow iframe embedding
@app.after_request
def add_iframe_headers(response):
    # Option 1: Allow any site to embed (simplest)
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    # Option 2: Restrict to your frontend domain (safer)
    # response.headers['X-Frame-Options'] = 'ALLOW-FROM https://your-frontend-domain.com'

    response.headers['Content-Security-Policy'] = "frame-ancestors *"
    return response



from user_upload import user_bp
from command_module import command_bp
from insta_routes.insta_receive import instagram_receive
from insta_routes.insta_receive_slot_test import instagram_receive_slot_test

# from scraper_api import scraper_bp
# Register the blueprint
app.register_blueprint(user_bp)
app.register_blueprint(command_bp)
app.register_blueprint(instagram_receive)
app.register_blueprint(instagram_receive_slot_test)

# app.register_blueprint(scraper_bp)

if __name__ == "__main__":
    # threading.Thread(target=cleanup_job, daemon=True).start()
    app.run(host="0.0.0.0", port=7777, debug=True)
