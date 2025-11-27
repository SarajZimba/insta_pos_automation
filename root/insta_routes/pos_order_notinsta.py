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
import os
# from ask_menu import ask_menu
# from ask_image import ask_image


API_URL = os.getenv('API_URL')

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
# ------------------------------
# Connect to Redis
# r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# @app.route("/ask", methods=["POST"])
# def ask_question():
#     try:
#         data = request.get_json()
#         question = data.get("question")
#         doc_id = data.get("doc_id")
#         document_outlet_name = data.get("document_outlet_name", None)
#         if not question:
#             return jsonify({"error": "Question is required"}), 400

#         context = ""
#         if doc_id:
#             try:
#                 chunks, index = load_document_from_db(doc_id, document_outlet_name)
#                 q_embed = embedder.encode([question])
#                 D, I = index.search(q_embed, k=3)
#                 context = " ".join([chunks[i] for i in I[0]])
#             except Exception as e:
#                 print(e)
#                 return jsonify({"error": "Document not found or failed to load"}), 404

#         # Hybrid: pass context if available, else fallback
#         answer = query_llama(context, question, model="llama3.2:3b")

#         return jsonify({
#             "question": question,
#             "doc_id": doc_id,
#             "document_outlet_name": document_outlet_name,
#             "answer": answer
#         })
#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

import redis
import datetime

r = redis.Redis(host='localhost', port=6379, db=1, decode_responses=True)

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





# ------------------------------
# Connect to Redis
# r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# ------------------------------

# def query_llama_with_slots(context, question, slots):
#     """
#     Calls LLaMA with context + question, and tries to extract slot values.

#     Args:
#         context (str): Text context from documents.
#         question (str): User's question.
#         slots (dict): Current slots, e.g., {"name": None, "date": None, "time": None}

#     Returns:
#         answer (str): LLaMA answer.
#         updated_slots (dict): Slots updated if LLaMA finds values.
#     """
#     # Step 1: Build a prompt for LLaMA to fill slots
#     slot_instructions = "\n".join([f"{k}: {v if v else '[empty]'}" for k, v in slots.items()])
#     prompt = (
#         f"You are a helpful assistant. Fill the following information from the user's input if available.\n"
#         f"Current slots:\n{slot_instructions}\n\n"
#         f"Context:\n{context}\n\n"
#         f"Question: {question}\n\n"
#         f"Provide the updated slot values in format:\n"
#         f"name=..., date=..., time=..., service_type=...\n"
#         f"And also answer the question."
#     )

#     # Step 2: Call your existing query_llama function
#     output = query_llama(question + "\n" + context + "\n" + prompt, question)

#     # Step 3: Extract slot values from output using regex
#     updated_slots = slots.copy()
#     for slot_name in slots.keys():
#         match = re.search(f"{slot_name}=([^,\\n]+)", output, re.IGNORECASE)
#         if match:
#             value = match.group(1).strip()
#             if value.lower() not in ["none", "[empty]"]:
#                 updated_slots[slot_name] = value

#     # Step 3.5: Validate date slot (accept only Monday–Friday)
#     date_value = updated_slots.get("date")
#     if date_value:
#         try:
#             date_obj = datetime.datetime.strptime(date_value, "%Y-%m-%d")
#             if date_obj.weekday() >= 5:  # 5=Saturday, 6=Sunday
#                 updated_slots["date"] = None  # invalid day
#         except ValueError:
#             updated_slots["date"] = None  # invalid format

#     # Step 4: Return LLaMA answer + updated slots
#     return output, updated_slots


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


# @app.route("/ask-outlet-command-slots", methods=["POST"])
# def ask_outlet_command_slots():
#     try:
#         data = request.get_json()
#         document_outlet_name = data.get("document_outlet_name")
#         user_id = data.get("user_id")
#         command_id = data.get("command_id")  # selected command by user
#         user_slots = data.get("slots", {})   # optional new slot values
#         question = data.get("question", "")  # user question for LLaMA

#         if not document_outlet_name or not user_id or not command_id:
#             return jsonify({"error": "document_outlet_name, user_id, and command_id are required"}), 400

#         session_key = f"{document_outlet_name}_{user_id}_{command_id}"

#         # Load current session from Redis
#         session_json = r.get(session_key)
#         session_slots = json.loads(session_json) if session_json else {}

#         # Update session slots with frontend values
#         session_slots.update(user_slots)

#         # Fetch required slots for this command from DB
#         slots_required = get_slots_for_command(command_id)
#         slots_dict = {slot["slot_name"]: session_slots.get(slot["slot_name"]) for slot in slots_required}

#         # Check if all required slots are filled
#         ready_to_call_api = all(v is not None and v != "" for v in slots_dict.values())

#         # Determine if this command has subcommands
#         conn = get_db_connection()
#         cursor = conn.cursor(dictionary=True)  # <-- important!
#         cursor.execute("SELECT COUNT(*) AS count FROM outlet_commands WHERE parent_command_id = %s", (command_id,))
#         subcommand_count = cursor.fetchone()["count"]


#         is_last_command = subcommand_count == 0

#         # Optionally call LLaMA if it's actionable and has no slots
#         llama_answer = None
#         if is_last_command and not slots_dict:
#             # Load document context

#             cursor.execute("SELECT command_text FROM outlet_commands WHERE command_id=%s", (command_id,))
#             row = cursor.fetchone()

#             # Use command_text as the question if frontend didn't provide one
#             if row and row.get("command_text"):
#                 question = row["command_text"]

#             try:
#                 chunks, index = load_document_from_db_outletwise(document_outlet_name)
#                 context = " ".join(chunks)  # simple concat; you can use vector search if needed
#                 llama_answer = query_llama_with_no_slots(context, question)
#             except Exception as e:
#                 llama_answer = f"No document context found: {str(e)}"
#         cursor.close()
#         conn.close()

#         # Save back to Redis (expires in 1 hour)
#         r.set(session_key, json.dumps(slots_dict), ex=3600)

#         return jsonify({
#             "document_outlet_name": document_outlet_name,
#             "command_id": command_id,
#             "slots": slots_dict,
#             "ready_to_call_api": ready_to_call_api,
#             "is_last_command": is_last_command,
#             "llama_answer": llama_answer
#         }), 200

#     except Exception as e:
#         return jsonify({"error": str(e)}), 500

import re
import requests

# NEGATIVE_PATTERNS = [
#     r"\bdo not show\b",
#     r"\bdon't show\b",
#     r"\bhide\b",
#     r"\bnot interested\b",
#     r"\bskip\b",
#     r"\bno products\b",
#     r"\bno items\b"
# ]

# PRODUCT_PATTERNS = [
#     r"\bproduct\b",
#     r"\bproducts\b",
#     r"\bitem\b",
#     r"\bitems\b",
#     r"\bmenu\b",
#     r"\bmenus\b",
#     r"\bdish\b",
#     r"\bdishes\b",
#     r"\bfood\b"
#     r"\bfoods\b"
# ]

# CATEGORY_PATTERNS = [
#     r"\bcategory\b",
#     r"\bcategories\b",
#     r"\bgroup\b",
#     r"\bgroups\b",
#     r"\bsection\b",
#     r"\bsections\b",
#     r"\btype\b",
#     r"\btypes\b"
# ]

def contains_pattern(text, patterns):
    """Check if any regex pattern matches in the text."""
    text = text.lower()
    return any(re.search(pat, text) for pat in patterns)


import redis
import datetime
import json
import requests

# r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

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

            # # --- Product & Category intent detection ---
            # wants_products = contains_pattern(question_lower, PRODUCT_PATTERNS)
            # wants_categories = contains_pattern(question_lower, CATEGORY_PATTERNS)
            # negative_intent = contains_pattern(question_lower, NEGATIVE_PATTERNS)

            # llama_answer = None

            # # ------------------------------
            # # Product intent
            # if (wants_products or wants_categories) and not negative_intent:


            #     # --- Load previous conversation context ---
            #     session_key = f"session:{document_outlet_name}:{user_id}"
            #     previous_msgs = r.lrange(session_key, -5, -1)
            #     previous_context = " ".join(
            #         [json.loads(m)["question"] + " " + json.loads(m)["answer"] for m in previous_msgs]
            #     )

            #     # --- Ask LLaMA if user wants products or categories ---
            #     llama_intent_prompt = (
            #         f"You are an assistant for a restaurant outlet. Decide the user's intent "
            #         f"based on the question. Only respond with JSON in the format:\n"
            #         f"{{\"intent\": \"show_products\" | \"show_categories\" | \"none\", "
            #         f"\"category_filter\": <category name or null>, "
            #         f"\"negative_intent\": true|false}}\n\n"
            #         f"Question: {question}\n\n"
            #         f"Answer:"
            #     )
            #     llama_intent_raw = query_llama(previous_context, llama_intent_prompt)
            #     try:
            #         llama_intent = json.loads(llama_intent_raw)
            #     except Exception:
            #         llama_intent = {"intent": "none", "category_filter": None, "negative_intent": False}

            #     intent = llama_intent.get("intent", "none")
            #     category_filter = llama_intent.get("category_filter")
            #     negative_intent = llama_intent.get("negative_intent", False)

            #     products = []
            #     categories = []
            #     llama_answer = None

            #     if not negative_intent:
            #         # --- Fetch categories + products ---
            #         try:
            #             resp = requests.get("http://103.250.132.116:8077/api/categories/", timeout=5)
            #             all_categories = resp.json() if resp.status_code == 200 else []

            #             if intent == "show_categories":
            #                 categories = all_categories
            #                 # llama_answer = query_llama_with_no_slots(previous_context, question)
            #                 llama_answer = f"Here are our categories"

            #             elif intent == "show_products":
            #                 for cat in all_categories:
            #                     if category_filter and category_filter.lower() not in cat["title"].lower():
            #                         continue
            #                     for prod in cat.get("products", []):
            #                         products.append(prod)
            #                 # llama_answer = query_llama_with_no_slots(previous_context, question)
            #                 if category_filter:
            #                     llama_answer = f"Here are our products of {category_filter}"
            #                 else:
            #                     llama_answer = f"Here are our products"



            #         except Exception as e:
            #             return jsonify({"error": f"Failed to fetch catalog: {str(e)}"}), 500

                # # --- Fallback answer ---
                # if not llama_answer:
                #     llama_answer = query_llama(previous_context, question)

                # # --- Save conversation ---
                # message = {
                #     "question": question,
                #     "answer": llama_answer,
                #     "timestamp": datetime.datetime.now().isoformat()
                # }
                # r.rpush(session_key, json.dumps(message))
                # r.ltrim(session_key, -5, -1)

                # # --- Return response ---
                # return jsonify({
                #     "document_outlet_name": document_outlet_name,
                #     "llama_answer": llama_answer,
                #     "categories": categories if categories else None,
                #     "products": products if products else None,
                #     "ready_to_call_api": intent in ["show_products", "show_categories"] and not negative_intent
                # }), 200

            # except Exception as e:
            #     return jsonify({"error": str(e)}), 500
                # try:
                #     product_api_url = "http://103.250.132.116:8077/api/product-list/"
                #     resp = requests.get(product_api_url, timeout=5)
                #     products = resp.json() if resp.status_code == 200 else []

                #     matched_keywords = [kw for kw in ["product", "item", "menu", "dish", "food"] if kw in question_lower]
                #     user_keyword = matched_keywords[0] if matched_keywords else "product"

                #     product_context = f"{previous_context} Here are the {user_keyword}s available in our outlet."
                #     try:
                #         llama_answer = query_llama_with_no_slots(product_context, question)
                #     except Exception:
                #         llama_answer = f"Here are the {user_keyword}s:"

                #     # 📝 Save message to Redis
                #     message = {
                #         "question": question,
                #         "answer": llama_answer,
                #         "timestamp": datetime.datetime.now().isoformat()
                #     }
                #     r.rpush(session_key, json.dumps(message))
                #     r.ltrim(session_key, -5, -1)

                #     return jsonify({
                #         "document_outlet_name": document_outlet_name,
                #         "command_id": None,
                #         "slots": {},
                #         "ready_to_call_api": True,
                #         "is_last_command": True,
                #         "llama_answer": llama_answer,
                #         "products": products
                #     }), 200
                # except Exception as e:
                #     return jsonify({"error": f"Failed to fetch products: {str(e)}"}), 500

            # # ------------------------------
            # # Category intent
            # if wants_categories and not negative_intent:
            #     try:
            #         category_api_url = "http://103.250.132.116:8077/api/categories/"
            #         resp = requests.get(category_api_url, timeout=5)
            #         categories = resp.json() if resp.status_code == 200 else []

            #         matched_keywords = [kw for kw in ["category", "group", "section", "type"] if kw in question_lower]
            #         user_keyword = matched_keywords[0] if matched_keywords else "category"

            #         category_context = f"{previous_context} Here are the available {user_keyword}s in our outlet."
            #         try:
            #             llama_answer = query_llama_with_no_slots(category_context, question)
            #         except Exception:
            #             llama_answer = f"Here are the {user_keyword}s:"

            #         # 📝 Save message to Redis
            #         message = {
            #             "question": question,
            #             "answer": llama_answer,
            #             "timestamp": datetime.datetime.now().isoformat()
            #         }
            #         r.rpush(session_key, json.dumps(message))
            #         r.ltrim(session_key, -5, -1)

            #         return jsonify({
            #             "document_outlet_name": document_outlet_name,
            #             "command_id": None,
            #             "slots": {},
            #             "ready_to_call_api": True,
            #             "is_last_command": True,
            #             "llama_answer": llama_answer,
            #             "categories": categories
            #         }), 200
            #     except Exception as e:
            #         return jsonify({"error": f"Failed to fetch categories: {str(e)}"}), 500

            # ------------------------------
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


# import redis
# import datetime
# import json
# import requests
# from flask import Flask, request, jsonify

# r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# def clean_output(text):
#     return text.strip()

# @app.route("/ask-outlet-command-slots", methods=["POST"])
# def ask_outlet_command_slots():
#     try:
#         data = request.get_json()
#         document_outlet_name = data.get("document_outlet_name")
#         user_id = data.get("user_id")
#         command_id = data.get("command_id")  # can be None
#         user_slots = data.get("slots", {})   # optional new slot values
#         question = data.get("question", "")  # user question for LLaMA

#         if not document_outlet_name or not user_id:
#             return jsonify({"error": "document_outlet_name and user_id are required"}), 400

#         # --- Load previous conversation context ---
#         session_key = f"session:{document_outlet_name}:{user_id}"
#         previous_msgs = r.lrange(session_key, -5, -1)
#         previous_context = " ".join([json.loads(m)["question"] + " " + json.loads(m)["answer"] for m in previous_msgs])

#         # -----------------------------------------------------------
#         # 📌 1. General conversation (no command_id)
#         # -----------------------------------------------------------
#         if not command_id and question:
#             llama_intent_prompt = (
#                 f"You are an assistant for a restaurant outlet. Decide the user's intent "
#                 f"based on the question. Only respond with JSON in the format:\n"
#                 f"{{\"intent\": \"show_products\" | \"show_categories\" | \"none\", "
#                 f"\"category_filter\": <category name or null>, "
#                 f"\"negative_intent\": true|false}}\n\n"
#                 f"Question: {question}\n\n"
#                 f"Answer:"
#             )
#             llama_intent_raw = query_llama(previous_context, llama_intent_prompt)
#             try:
#                 llama_intent = json.loads(llama_intent_raw)
#             except Exception:
#                 llama_intent = {"intent": "none", "category_filter": None, "negative_intent": False}

#             intent = llama_intent.get("intent", "none")
#             category_filter = llama_intent.get("category_filter")
#             negative_intent = llama_intent.get("negative_intent", False)

#             products, categories, llama_answer = [], [], None

#             if not negative_intent:
#                 try:
#                     resp = requests.get("http://103.250.132.116:8077/api/categories/", timeout=5)
#                     all_categories = resp.json() if resp.status_code == 200 else []

#                     if intent == "show_categories":
#                         categories = all_categories
#                         llama_answer = f"Here are our categories"

#                     elif intent == "show_products":
#                         for cat in all_categories:
#                             if category_filter and category_filter.lower() not in cat["title"].lower():
#                                 continue
#                             for prod in cat.get("products", []):
#                                 products.append(prod)
#                         llama_answer = f"Here are our products of {category_filter or 'all categories'}"

#                 except Exception as e:
#                     return jsonify({"error": f"Failed to fetch catalog: {str(e)}"}), 500

#             # Fallback answer using LLaMA
#             if not llama_answer:
#                 llama_answer = query_llama(previous_context, question)

#             # Save conversation
#             message = {
#                 "question": question,
#                 "answer": llama_answer,
#                 "timestamp": datetime.datetime.now().isoformat()
#             }
#             r.rpush(session_key, json.dumps(message))
#             r.ltrim(session_key, -5, -1)

#             return jsonify({
#                 "document_outlet_name": document_outlet_name,
#                 "command_id": None,
#                 "slots": {},
#                 "ready_to_call_api": intent in ["show_products", "show_categories"] and not negative_intent,
#                 "is_last_command": True,
#                 "llama_answer": llama_answer,
#                 "categories": categories if categories else None,
#                 "products": products if products else None
#             }), 200

#         # -----------------------------------------------------------
#         # 📌 2. Normal command flow (with command_id)
#         # -----------------------------------------------------------
#         session_key = f"{document_outlet_name}_{user_id}_{command_id}"
#         session_json = r.get(session_key)
#         session_slots = json.loads(session_json) if session_json else {}
#         session_slots.update(user_slots)

#         slots_required = get_slots_for_command(command_id)
#         slots_dict = {slot["slot_name"]: session_slots.get(slot["slot_name"]) for slot in slots_required}
#         ready_to_call_api = all(v is not None and v != "" for v in slots_dict.values())

#         conn = get_db_connection()
#         cursor = conn.cursor(dictionary=True)
#         cursor.execute("SELECT COUNT(*) AS count FROM outlet_commands WHERE parent_command_id = %s", (command_id,))
#         subcommand_count = cursor.fetchone()["count"]
#         is_last_command = subcommand_count == 0

#         llama_answer = None
#         if is_last_command and not slots_dict:
#             cursor.execute("SELECT command_text FROM outlet_commands WHERE command_id=%s", (command_id,))
#             row = cursor.fetchone()
#             if row and row.get("command_text") and not question:
#                 question = row["command_text"]

#             try:
#                 chunks, index = load_document_from_db_outletwise(document_outlet_name)
#                 context = " ".join(chunks)
#                 llama_answer = query_llama_with_no_slots(context, question)
#             except Exception as e:
#                 llama_answer = f"No document context found: {str(e)}"

#         cursor.close()
#         conn.close()
#         r.set(session_key, json.dumps(slots_dict), ex=3600)

#         return jsonify({
#             "document_outlet_name": document_outlet_name,
#             "command_id": command_id,
#             "slots": slots_dict,
#             "ready_to_call_api": ready_to_call_api,
#             "is_last_command": is_last_command,
#             "llama_answer": llama_answer
#         }), 200

#     except Exception as e:
#         return jsonify({"error": str(e)}), 500






@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "POS (Llama) is running!"})


# @app.route("/ask-menu", methods=["POST"])
# def ask_menu_endpoint():
#     data = request.get_json()
#     question = data.get("question")
#     if not question:
#         return jsonify({"error": "Question is required"}), 400

#     response = ask_menu(question)
#     return jsonify(response)


# @app.route("/ask-image-upload", methods=["POST"])
# def ask_image_upload():
#     if "file" not in request.files or "username" not in request.form:
#         return jsonify({"error": "File and username are required"}), 400

#     image_file = request.files["file"]
#     username = request.form["username"]
#     image_path = f"/tmp/{image_file.filename}"
#     image_file.save(image_path)

#     response = ask_image(image_path)  # call existing ask_image.py function
#     if "error" in response:
#         return jsonify(response), 400

#     # Save detected text in DB
#     image_id = save_image_text(username, image_file.filename, response["detected_text"])

#     return jsonify({
#         "image_id": image_id,
#         "detected_text": response["detected_text"],
#         "explanation": response["explanation"],
#         "message": "Image processed and stored successfully"
#     })


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


# @app.route("/outlet-catalog", methods=["POST"])
# def outlet_catalog():
#     try:
#         data = request.get_json()
#         document_outlet_name = data.get("document_outlet_name")
#         user_id = data.get("user_id")
#         question = data.get("question", "")

#         if not document_outlet_name or not user_id:
#             return jsonify({"error": "document_outlet_name and user_id are required"}), 400

#         # --- Redis Session Keys ---
#         session_key = f"session:{document_outlet_name}:{user_id}"
#         session_state_key = f"state:{document_outlet_name}:{user_id}"

#         # --- Load previous conversation context ---
#         previous_msgs = r.lrange(session_key, -5, -1)
#         previous_context = " ".join(
#             [json.loads(m)["question"] + " " + json.loads(m)["answer"] for m in previous_msgs]
#         )

#         # --- Load document knowledge (if available) ---
#         knowledge_context = ""
#         try:
#             conn = get_db_connection()
#             cursor = conn.cursor(dictionary=True)
#             cursor.execute(
#                 "SELECT id FROM documents WHERE document_outlet_name=%s LIMIT 1",
#                 (document_outlet_name,)
#             )
#             row = cursor.fetchone()
#             cursor.close()
#             conn.close()

#             if row:
#                 doc_id = row["id"]
#                 chunks, index = load_document_from_db(doc_id, document_outlet_name)
#                 q_embed = embedder.encode([question])
#                 D, I = index.search(q_embed, k=5)
#                 retrieved_chunks = [chunks[i] for i in I[0]]
#                 knowledge_context = " ".join(retrieved_chunks)
#         except Exception as e:
#             print(f"[Document Retrieval Warning] {e}")

#         # --- Determine user intent using LLaMA ---
#         llama_intent_prompt = (
#             f"You are an assistant for a restaurant outlet. Decide the user's intent "
#             f"based on the question. Only respond with JSON in the format:\n"
#             f"{{\"intent\": \"show_products\" | \"show_categories\" | \"none\", "
#             f"\"category_filter\": <category name or null>, "
#             f"\"negative_intent\": true|false}}\n\n"
#             f"Question: {question}\n\n"
#             f"Answer:"
#         )
#         llama_intent_raw = query_llama(previous_context + " " + knowledge_context, llama_intent_prompt)
#         try:
#             llama_intent = json.loads(llama_intent_raw)
#         except Exception:
#             llama_intent = {"intent": "none", "category_filter": None, "negative_intent": False}

#         intent = llama_intent.get("intent", "none")
#         category_filter = llama_intent.get("category_filter")
#         negative_intent = llama_intent.get("negative_intent", False)

#         products = []
#         categories = []
#         llama_answer = None

#         # --- If user asks for products, first show categories only ---
#         if intent == "show_products" and not category_filter:
#             r.set(session_state_key, "awaiting_category_selection")
#             resp = requests.get("http://103.250.132.116:8077/api/categories/", timeout=5)
#             all_categories = resp.json() if resp.status_code == 200 else []

#             return jsonify({
#                 "llama_answer": "Here are our categories. Please choose one.",
#                 "categories": all_categories,
#                 "products": None,
#                 "ready_to_call_api": False
#             }), 200

#         # --- If user is selecting a category after categories were shown ---
#         last_state = r.get(session_state_key)
#         if last_state == b"awaiting_category_selection" and category_filter:
#             r.delete(session_state_key)  # Clear state

#             resp = requests.get("http://103.250.132.116:8077/api/categories/", timeout=5)
#             all_categories = resp.json() if resp.status_code == 200 else []

#             # Match category from user input
#             for cat in all_categories:
#                 if category_filter.lower() in cat["title"].lower():
#                     selected_category = cat
#                     products = cat.get("products", [])
#                     llama_answer = f"Here are products from {cat['title']}"
#                     break

#             if not products:
#                 return jsonify({
#                     "llama_answer": "I couldn't find that category. Please choose one from the list.",
#                     "categories": all_categories,
#                     "products": None,
#                     "ready_to_call_api": False
#                 }), 200

#             # Return filtered products
#             return jsonify({
#                 "llama_answer": llama_answer,
#                 "categories": None,
#                 "products": products,
#                 "ready_to_call_api": True
#             }), 200

#         # --- Standard flow (for categories or fallback) ---
#         if not negative_intent:
#             try:
#                 resp = requests.get("http://103.250.132.116:8077/api/categories/", timeout=5)
#                 all_categories = resp.json() if resp.status_code == 200 else []

#                 if intent == "show_categories":
#                     categories = all_categories
#                     llama_answer = "Here are our categories"

#                 elif intent == "show_products" and category_filter:
#                     for cat in all_categories:
#                         if category_filter.lower() in cat["title"].lower():
#                             products = cat.get("products", [])
#                     llama_answer = f"Here are our products of {category_filter}"

#             except Exception as e:
#                 return jsonify({"error": f"Failed to fetch catalog: {str(e)}"}), 500

#         if not llama_answer:
#             llama_answer = query_llama(previous_context + " " + knowledge_context, question)

#         # --- Save conversation ---
#         message = {
#             "question": question,
#             "answer": llama_answer,
#             "timestamp": datetime.datetime.now().isoformat()
#         }
#         r.rpush(session_key, json.dumps(message))
#         r.ltrim(session_key, -5, -1)

#         # --- Final Response ---
#         return jsonify({
#             "document_outlet_name": document_outlet_name,
#             "llama_answer": llama_answer,
#             "categories": categories if categories else None,
#             "products": products if products else None,
#             "ready_to_call_api": intent in ["show_products", "show_categories"] and not negative_intent
#         }), 200

#     except Exception as e:
#         return jsonify({"error": str(e)}), 500


import json
import re
import datetime
import difflib
from flask import request, jsonify
import requests
import spacy  # ✅ NEW
# --- Keep your existing helpers: get_db_connection, embedder, query_llama, load_document_from_db, r (redis) ---

CATEGORIES_API_URL = API_URL + "api/categories/"
PRODUCTS_API_URL = API_URL + "api/product-list/"
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

    # # 3) whole-word equality (all filter tokens must be in category)
    # filter_tokens = set(norm_filter.split())
    # for norm_title, cat in normalized_map:
    #     title_tokens = set(norm_title.split())
    #     if filter_tokens <= title_tokens:
    #         return cat

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

# @app.route("/outlet-catalog", methods=["POST"])
# def outlet_catalog():
#     try:
#         data = request.get_json(force=True)
#         document_outlet_name = data.get("document_outlet_name")
#         user_id = data.get("user_id")
#         question = data.get("question", "").strip()

#         if not document_outlet_name or not user_id:
#             return jsonify({"error": "document_outlet_name and user_id are required"}), 400

#         # Redis keys
#         session_key = f"session:{document_outlet_name}:{user_id}"
#         session_state_key = f"state:{document_outlet_name}:{user_id}"

#         # Previous conversation context
#         previous_msgs = r.lrange(session_key, -MAX_PREV_CTX, -1) or []
#         previous_context = " ".join(
#             [(json.loads(m)["question"] + " " + json.loads(m)["answer"]) for m in previous_msgs if m]
#         )

#         # Optional document retrieval for RAG
#         knowledge_context = ""
#         try:
#             conn = get_db_connection()
#             cursor = conn.cursor(dictionary=True)
#             cursor.execute("SELECT id FROM documents WHERE document_outlet_name=%s LIMIT 1", (document_outlet_name,))
#             row = cursor.fetchone()
#             cursor.close()
#             conn.close()

#             if row:
#                 doc_id = row["id"]
#                 chunks, index = load_document_from_db(doc_id, document_outlet_name)

#                 q_embed = embedder.encode([question])
#                 D, I = index.search(q_embed, k=5)
#                 retrieved_chunks = [chunks[i] for i in I[0] if i < len(chunks)]
#                 knowledge_context = " ".join(retrieved_chunks)

#         except Exception as e:
#             print(f"[Document Retrieval Warning] {e}")

#         # Get intent from model
#         llama_intent_prompt = (
#             f"You are an assistant for a restaurant outlet. Decide the user's intent.\n"
#             f"ONLY return JSON:\n"
#             f"{{\"intent\": \"show_products\" | \"show_categories\" | \"none\", "
#             f"\"category_filter\": <string or null>, "
#             f"\"negative_intent\": true|false}}\n\n"
#             f"User Question: {question}\nAnswer:"
#         )

#         llama_intent_raw = query_llama(previous_context + " " + knowledge_context, llama_intent_prompt)
#         llama_intent = parse_llama_intent(llama_intent_raw)

#         intent = llama_intent.get("intent", "none")
#         category_filter = llama_intent.get("category_filter")
#         negative_intent = llama_intent.get("negative_intent", False)

#         # Fetch categories
#         all_categories = fetch_all_categories()

#         # --- STATE HANDLING ---
#         last_state = r.get(session_state_key)

#         # Case: Model wants products but no category → Ask user to choose a category
#         if intent == "show_products" and not category_filter:
#             r.set(session_state_key, "awaiting_category_selection")
#             return jsonify({
#                 "llama_answer": "Here are our categories. Please choose one.",
#                 "categories": all_categories,
#                 "products": None,
#                 "ready_to_call_api": False
#             }), 200

#         # Case: User previously asked category and now responded with name
#         if last_state == b"awaiting_category_selection" and category_filter:
#             r.delete(session_state_key)
#             matched = match_category_by_filter(category_filter, all_categories)
#             if not matched:
#                 return jsonify({
#                     "llama_answer": "I couldn't find that category. Please choose again.",
#                     "categories": all_categories,
#                     "products": None,
#                     "ready_to_call_api": False
#                 }), 200

#             return jsonify({
#                 "llama_answer": f"Here are products from {matched['title']}",
#                 "categories": None,
#                 "products": matched.get("products", []),
#                 "ready_to_call_api": True
#             }), 200
#         # ✅ Handle negative intent early
#         if negative_intent:
#             llama_answer = "No problem 😊 If you need anything later, just let me know."
            
#             # Save to session
#             msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
#             r.rpush(session_key, json.dumps(msg))
#             r.ltrim(session_key, -MAX_PREV_CTX, -1)

#             return jsonify({
#                 "document_outlet_name": document_outlet_name,
#                 "llama_answer": llama_answer,
#                 "categories": None,
#                 "products": None,
#                 "ready_to_call_api": False
#             }), 200

#         # --- NORMAL INTENT PROCESSING ---
#         products = []
#         categories = []
#         llama_answer = None

#         if not negative_intent:
#             if intent == "show_categories":
#                 categories = all_categories
#                 llama_answer = "Here are our categories"

#             elif intent == "show_products" and category_filter:
#                 matched = match_category_by_filter(category_filter, all_categories)
#                 if matched:
#                     products = matched.get("products", [])
#                     llama_answer = f"Here are products from {matched['title']}"

#         # --- FALLBACK MATCH ---
#         if not products and not categories:
#             # Try category first
#             fallback_match = match_category_by_filter(question, all_categories)
#             if fallback_match:
#                 products = fallback_match.get("products", [])
#                 llama_answer = f"Here are products from {fallback_match['title']}"
#             else:
#                 # Then try product
#                 prod_match, prod_category = match_product_by_filter(question, all_categories)
#                 if prod_match:
#                     products = [prod_match]
#                     llama_answer = f"Yes! We have '{prod_match['title']}' in category '{prod_category['title']}'"

#             if products:
#                 return jsonify({
#                     "document_outlet_name": document_outlet_name,
#                     "llama_answer": llama_answer,
#                     "categories": None,
#                     "products": products,
#                     "ready_to_call_api": True
#                 }), 200


#         # --- FINAL FALLBACK → CHATBOT ANSWER ---
#         if not llama_answer:
#             try:
#                 llama_answer = query_llama(previous_context + " " + knowledge_context, question)
#             except Exception as e:
#                 print(f"[llama fallback error] {e}")
#                 llama_answer = "I'm not sure. Could you clarify?"

#         # Save conversation
#         msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
#         r.rpush(session_key, json.dumps(msg))
#         r.ltrim(session_key, -MAX_PREV_CTX, -1)

#         return jsonify({
#             "document_outlet_name": document_outlet_name,
#             "llama_answer": llama_answer,
#             "categories": categories or None,
#             "products": products or None,
#             "ready_to_call_api": bool(products or categories)
#         }), 200

#     except Exception as e:
#         print(f"[outlet_catalog ERROR] {e}")
#         return jsonify({"error": str(e)}), 500

# def resolve_categories(text, nlp, all_categories):
#     doc = nlp(text)
    
#     genders = []
#     products = []
#     exact_categories = []

#     for ent in doc.ents:
#         print(ent.text, ent.label_)


#     for ent in doc.ents:
#         if ent.label_ == "CATEGORY":
#             exact_categories.append(ent.text)
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_PRODUCT":
#             products.append(ent.text.lower())
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_GENDER":
#             genders.append(ent.text.lower())
    
#     matched_categories = []

#     # 1️⃣ If exact categories present
#     if exact_categories:
#         for cat in all_categories:
#             cat_title = cat.get("title", "")
#             if cat_title.lower() in [c.lower() for c in exact_categories]:
#                 matched_categories.append(cat)

#     # 2️⃣ If product root only
#     elif products:
#         for cat in all_categories:
#             cat_title = cat.get("title", "").lower()
#             if any(prod in cat_title for prod in products):
#                 matched_categories.append(cat)

#     # 3️⃣ If gender only
#     elif genders:
#         for cat in all_categories:
#             cat_title = cat.get("title", "").lower()
#             if any(g in cat_title for g in genders):
#                 matched_categories.append(cat)

#     # 4️⃣ If both product + gender
#     else:
#         for cat in all_categories:
#             cat_title = cat.get("title", "").lower()
#             if (not products or any(prod in cat_title for prod in products)) and \
#                (not genders or any(g in cat_title for g in genders)):
#                 matched_categories.append(cat)

#     return matched_categories

import re

# def resolve_categories(text, all_categories):
#     """
#     Extract categories from text using NER, handling ambiguous gender/product.
#     Returns a list of matched category dicts from all_categories.
#     """
#     doc = nlp_ner(text)
    
#     genders = []
#     products = []
#     exact_categories = []

#     for ent in doc.ents:
#         print(ent.text, ent.label_)

#     # Collect entities
#     for ent in doc.ents:
#         if ent.label_ == "CATEGORY":
#             exact_categories.append(ent.text)
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_PRODUCT":
#             products.append(ent.text.lower())
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_GENDER":
#             genders.append(ent.text.lower())

#     matched_categories = []

#     # 1️⃣ If exact categories are detected, match exactly
#     if exact_categories:
#         for cat in all_categories:
#             cat_title = cat.get("title", "")
#             if cat_title.lower() in [c.lower() for c in exact_categories]:
#                 matched_categories.append(cat)

#     # 2️⃣ If product root only
#     elif products and not genders:
#         for cat in all_categories:
#             cat_title = cat.get("title", "").lower()
#             for prod in products:
#                 if re.search(rf"\b{re.escape(prod)}\b", cat_title):
#                     matched_categories.append(cat)
#                     break

#     # 3️⃣ If gender only
#     elif genders and not products:
#         for cat in all_categories:
#             cat_title = cat.get("title", "").lower()
#             for g in genders:
#                 if re.search(rf"\b{re.escape(g)}\b", cat_title):
#                     matched_categories.append(cat)
#                     break

#     # 4️⃣ If both product + gender are present
#     elif products and genders:
#         for cat in all_categories:
#             cat_title = cat.get("title", "").lower()
#             prod_match = any(re.search(rf"\b{re.escape(p)}\b", cat_title) for p in products)
#             gender_match = any(re.search(rf"\b{re.escape(g)}\b", cat_title) for g in genders)
#             if prod_match and gender_match:
#                 matched_categories.append(cat)

#     return matched_categories


import re
import requests

def resolve_categories(text, all_categories):
    """
    Extract categories + promotional intent from text using NER.
    Returns matched category list or promotional grouped results.
    """
    doc = nlp_ner(text)

    genders = []
    products = []
    exact_categories = []
    promotional_detected = False

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
        elif ent.label_ == "PROMOTIONAL":
            promotional_detected = True

    # ✅ If promotional intent is detected → Return promotional products instead
    if promotional_detected:
        print("🔹 PROMOTIONAL detected — fetching promotional products ...")

        product_list = requests.get(PRODUCTS_API_URL).json()

        # Filter only items where is_promo == true
        promo_products = [p for p in product_list if p.get("is_promo")]

        # Group promotional items by category title
        promo_by_category = {}
        for p in promo_products:
            cat = p["category"]["title"]
            promo_by_category.setdefault(cat, [])
            promo_by_category[cat].append(p)

        # Convert into return format similar to matched categories
        promo_category_result = []
        for cat, items in promo_by_category.items():
            promo_category_result.append({
                "title":"Clearance Sales",
                "products": items
            })

        return promo_category_result  # ✅ Return promotional category instead of normal matching

    # ✅ If no promotion intent → Perform normal category matching
    matched_categories = []

    # 1️⃣ Exact category detected
    if exact_categories:
        for cat in all_categories:
            cat_title = cat.get("title", "")
            if cat_title.lower() in [c.lower() for c in exact_categories]:
                matched_categories.append(cat)

    # 2️⃣ Product root only
    elif products and not genders:
        for cat in all_categories:
            cat_title = cat.get("title", "").lower()
            for prod in products:
                if re.search(rf"\b{re.escape(prod)}\b", cat_title):
                    matched_categories.append(cat)
                    break

    # 3️⃣ Gender only
    elif genders and not products:
        for cat in all_categories:
            cat_title = cat.get("title", "").lower()
            for g in genders:
                if re.search(rf"\b{re.escape(g)}\b", cat_title):
                    matched_categories.append(cat)
                    break

    # 4️⃣ Both product + gender
    elif products and genders:
        for cat in all_categories:
            cat_title = cat.get("title", "").lower()
            prod_match = any(re.search(rf"\b{re.escape(p)}\b", cat_title) for p in products)
            gender_match = any(re.search(rf"\b{re.escape(g)}\b", cat_title) for g in genders)
            if prod_match and gender_match:
                matched_categories.append(cat)

    return matched_categories



# import spacy
# from spacy.matcher import PhraseMatcher
# from spacy.tokens import Doc
# import re

# # Load your trained NER model
# nlp_ner = spacy.load("ner_model")  # path to your trained model

# # Load a blank or small English model for lemmatization & PhraseMatcher
# nlp_matcher = spacy.load("en_core_web_sm")  # lightweight model for lemmatization

# def build_phrasematcher(all_categories):
#     """
#     Build PhraseMatcher with all category titles (lowercased).
#     """
#     matcher = PhraseMatcher(nlp_matcher.vocab, attr="LOWER")
#     patterns = [nlp_matcher.make_doc(cat["title"]) for cat in all_categories]
#     matcher.add("CATEGORY_MATCHER", patterns)
#     return matcher

# def resolve_categories(text, all_categories):
#     """
#     Extract categories from text using NER + PhraseMatcher + lemmatization.
#     Returns a list of matched category dicts from all_categories.
#     """
#     # --- Step 1: NER detection ---
#     doc = nlp_ner(text)
    
#     genders = []
#     products = []
#     exact_categories = []

#     for ent in doc.ents:
#         print(ent.text, ent.label_)

#     for ent in doc.ents:
#         # Collect NER entities
#         if ent.label_ == "CATEGORY":
#             exact_categories.append(ent.text.lower())
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_PRODUCT":
#             products.append(ent.text.lower())
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_GENDER":
#             genders.append(ent.text.lower())

#     matched_categories = []

#     # --- Step 2: Exact category matches from NER ---
#     if exact_categories:
#         for cat in all_categories:
#             if cat["title"].lower() in exact_categories:
#                 matched_categories.append(cat)

#     # --- Step 3: PhraseMatcher for ambiguous products / plurals ---
#     matcher = build_phrasematcher(all_categories)

#     # Lemmatize the input text to handle plurals (kurthas -> kurtha)
#     doc_lem = nlp_matcher(text)
#     lemmatized_text = " ".join([token.lemma_ for token in doc_lem])
#     doc_for_match = nlp_matcher(lemmatized_text)

#     matches = matcher(doc_for_match)
#     for match_id, start, end in matches:
#         span_text = doc_for_match[start:end].text.lower()
#         for cat in all_categories:
#             if cat["title"].lower() == span_text and cat not in matched_categories:
#                 matched_categories.append(cat)

#     # --- Step 4: Handle gender + product combinations if NER caught ambiguous terms ---
#     if products or genders:
#         for cat in all_categories:
#             cat_title = cat["title"].lower()
#             prod_match = all(p in cat_title for p in products) if products else True
#             gender_match = all(g in cat_title for g in genders) if genders else True
#             if prod_match and gender_match and cat not in matched_categories:
#                 matched_categories.append(cat)

#     return matched_categories


# def normalize(text):
#     text = text.lower().strip()
#     # plural to singular normalization
#     if text.endswith("as"):
#         text = text[:-1]       # kurthas → kurtha
#     if text.endswith("s") and not text.endswith("ss"):
#         text = text[:-1]       # mens → men, shoes → shoe
#     return text

# def resolve_categories(text, nlp, all_categories):
#     doc = nlp(text)

#     genders = []
#     products = []
#     exact_categories = []

#     for ent in doc.ents:
#         print(ent.text, ent.label_)

#     # collect normalized entity values
#     for ent in doc.ents:
#         val = normalize(ent.text)

#         if ent.label_ == "CATEGORY":
#             exact_categories.append(val)
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_PRODUCT":
#             products.append(val)
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_GENDER":
#             genders.append(val)

#     matched_categories = []

#     # Normalize category titles in DB too
#     normalized_db = [
#         {**cat, "normalized_title": normalize(cat["title"])}
#         for cat in all_categories
#     ]

#     # 1) Exact category match
#     for ec in exact_categories:
#         for cat in normalized_db:
#             if ec in cat["normalized_title"]:
#                 matched_categories.append(cat)
    
#     # 2) Product only
#     if products and not genders:
#         for cat in normalized_db:
#             if any(p in cat["normalized_title"] for p in products):
#                 matched_categories.append(cat)

#     # 3) Gender only
#     if genders and not products:
#         for cat in normalized_db:
#             if any(g in cat["normalized_title"] for g in genders):
#                 matched_categories.append(cat)

#     # 4) Gender + Product combine
#     if products and genders:
#         for cat in normalized_db:
#             if any(p in cat["normalized_title"] for p in products) and any(g in cat["normalized_title"] for g in genders):
#                 matched_categories.append(cat)

#     # return list without temporary normalized field
#     return [{k:v for k,v in c.items() if k!="normalized_title"} for c in matched_categories]


# def resolve_categories(text, nlp, all_categories):
#     doc = nlp(text.lower())

#     genders = []
#     products = []

#     PRODUCT_ROOTS = ["kurtha", "shoe", "shoes"]  # ← include variations

#     # Extract entities
#     for ent in doc.ents:
#         if ent.label_ == "CATEGORY_AMBIGUOUS_GENDER":
#             genders.append(ent.text.lower())
#         elif ent.label_ == "CATEGORY_AMBIGUOUS_PRODUCT":
#             products.append(ent.text.lower())

#     # Also detect product by text scan if NER missed plural
#     for token in PRODUCT_ROOTS:
#         if token in text.lower():
#             products.append(token)

#     genders = list(set(genders))
#     products = list(set(products))

#     matched = []

#     if genders and products:
#         # EX: "mens kurtha"
#         for cat in all_categories:
#             title = cat["title"].lower()
#             if any(g in title for g in genders) and any(p in title for p in products):
#                 matched.append(cat)

#     elif genders:
#         # EX: "show mens"
#         for cat in all_categories:
#             title = cat["title"].lower()
#             if any(g in title for g in genders):
#                 matched.append(cat)

#     elif products:
#         # EX: "show kurtha"
#         for cat in all_categories:
#             title = cat["title"].lower()
#             if any(p in title for p in products):
#                 matched.append(cat)

#     else:
#         return all_categories  # fallback

#     return matched or all_categories



# @app.route("/outlet-catalog", methods=["POST"])
# def outlet_catalog():
#     try:
#         data = request.get_json(force=True)
#         document_outlet_name = data.get("document_outlet_name")
#         user_id = data.get("user_id")
#         question = data.get("question", "").strip()

#         if not document_outlet_name or not user_id:
#             return jsonify({"error": "document_outlet_name and user_id are required"}), 400

#         session_key = f"session:{document_outlet_name}:{user_id}"
#         session_state_key = f"state:{document_outlet_name}:{user_id}"

#         previous_msgs = r.lrange(session_key, -MAX_PREV_CTX, -1) or []
#         previous_context = " ".join(
#             [(json.loads(m)["question"] + " " + json.loads(m)["answer"]) for m in previous_msgs if m]
#         )

#         knowledge_context = ""

#         # Intent detection
#         llama_intent_prompt = (
#             f"You are an assistant for a clothing outlet.\n"
#             f"Return only JSON:\n"
#             f"{{\"intent\": \"show_products\" | \"show_categories\" | \"none\", "
#             f"\"category_filter\": <string or null>, "
#             f"\"negative_intent\": true|false}}\n\n"
#             f"User Question: {question}\nAnswer:"
#         )

#         llama_intent_raw = query_llama(previous_context + " " + knowledge_context, llama_intent_prompt)
#         llama_intent = parse_llama_intent(llama_intent_raw)

#         intent = llama_intent.get("intent", "none")
        
#         # ✅ FIRST: NER CATEGORY DETECTION
#         category_filter = extract_category_with_ner(question)


#         print("category filter", category_filter)
#         # ✅ SECOND: fallback to LLaMA result if NER found nothing
#         if not category_filter:
#             category_filter = llama_intent.get("category_filter")

#         negative_intent = llama_intent.get("negative_intent", False)

#         all_categories = fetch_all_categories()
#         last_state = r.get(session_state_key)

#         # Normal logic continues...
#         if negative_intent:
#             llama_answer = "No problem 😊 Let me know anytime."
#             msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
#             r.rpush(session_key, json.dumps(msg))
#             r.ltrim(session_key, -MAX_PREV_CTX, -1)
#             return jsonify({"llama_answer": llama_answer, "ready_to_call_api": False}), 200

#         products = []
#         categories = []
#         llama_answer = None

#         if intent == "show_categories":
#             categories = all_categories
#             llama_answer = "Here are our categories"

#         elif intent == "show_products" and category_filter:
#             matched = match_category_by_filter(category_filter, all_categories)
#             if matched:
#                 products = matched.get("products", [])
#                 llama_answer = f"Here are products from {matched['title']}"

#         if not products and not categories:
#             fallback_match = match_category_by_filter(question, all_categories)
#             if fallback_match:
#                 products = fallback_match.get("products", [])
#                 llama_answer = f"Here are products from {fallback_match['title']}"

#         if not llama_answer:
#             llama_answer = query_llama(previous_context, question)

#         msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
#         r.rpush(session_key, json.dumps(msg))
#         r.ltrim(session_key, -MAX_PREV_CTX, -1)

#         return jsonify({
#             "llama_answer": llama_answer,
#             "categories": categories or None,
#             "products": products or None,
#             "ready_to_call_api": bool(products or categories)
#         }), 200

#     except Exception as e:
#         print(f"[outlet_catalog ERROR] {e}")
#         return jsonify({"error": str(e)}), 500


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

def fuzzy_category_match(text, all_categories, threshold=80):
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

# from rapidfuzz import fuzz

# def fuzzy_match_products(question: str, all_categories: list, threshold: int = 75):
#     """
#     Fuzzy match the question against product titles.
    
#     Returns:
#         matched_categories: list of categories containing matched products
#         matched_products: list of matched products
#     """
#     question_normalized = question.lower()
#     matched_products = []
#     matched_categories_dict = {}

#     for cat in all_categories:
#         for prod in cat.get("products", []):
#             title = prod.get("title", "").lower()
#             similarity = fuzz.partial_ratio(question_normalized, title)
#             if similarity >= threshold:
#                 matched_products.append(prod)
#                 # Use category ID as key to avoid duplicates
#                 matched_categories_dict[cat["id"]] = cat

#     matched_categories = list(matched_categories_dict.values())
#     return matched_categories, matched_products

from rapidfuzz import fuzz
import copy

def fuzzy_match_products(question: str, all_categories: list, threshold: int = 60):
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

            print(f"similarity for prodct {title} {similarity}")
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



# @app.route("/outlet-catalog", methods=["POST"])
# def outlet_catalog():
#     try:
#         data = request.get_json(force=True)
#         document_outlet_name = data.get("document_outlet_name")
#         user_id = data.get("user_id")
#         question = data.get("question", "").strip()

#         if not document_outlet_name or not user_id:
#             return jsonify({"error": "document_outlet_name and user_id are required"}), 400

#         session_key = f"session:{document_outlet_name}:{user_id}"
#         session_state_key = f"state:{document_outlet_name}:{user_id}"

#         previous_msgs = r.lrange(session_key, -MAX_PREV_CTX, -1) or []
#         previous_context = " ".join(
#             [(json.loads(m)["question"] + " " + json.loads(m)["answer"]) for m in previous_msgs if m]
#         )

#         knowledge_context = ""

#         # Intent detection using LLaMA
#         llama_intent_prompt = (
#             f"You are an assistant for a clothing outlet.\n"
#             f"Return only JSON:\n"
#             f"{{\"intent\": \"show_products\" | \"show_categories\" | \"none\", "
#             f"\"category_filter\": <string or null>, "
#             f"\"negative_intent\": true|false}}\n\n"
#             f"User Question: {question}\nAnswer:"
#         )
#         llama_intent_raw = query_llama(previous_context + " " + knowledge_context, llama_intent_prompt)
#         llama_intent = parse_llama_intent(llama_intent_raw)
#         intent = llama_intent.get("intent", "none")
#         negative_intent = llama_intent.get("negative_intent", False)

#         # Fetch all categories from API
#         all_categories = fetch_all_categories()
#         question_normalized = question.lower()
#         print("question normalized", question_normalized)
#         # --- NER-based category detection ---
#         matched_categories = resolve_categories(question_normalized, all_categories)
#         print("intent", llama_intent)
#         print("matched_categories", matched_categories)

#         # Fallback using PhraseMatcher if NER fails
#         if not matched_categories:
#             matched_categories = match_categories_phrasematcher(question_normalized, all_categories)
#             print("PhraseMatcher fallback matched categories:", matched_categories)

#         # 3) If still empty → Fuzzy fallback (handles spelling errors)
#         if not matched_categories:
#             matched_categories = fuzzy_category_match(question_normalized, all_categories, threshold=80)
#             print("Fuzzy fallback:", matched_categories)

#         # 4) If still empty → Fuzzy match product titles
#         if not matched_categories:
#             matched_categories, products = fuzzy_match_products(question, all_categories, threshold=60)
#             print("Fuzzy fallback on categories:", matched_categories)
#             print("Fuzzy fallback on products:", products)

#         products = []
#         categories = []
#         llama_answer = None

#         # Case: show categories
#         if intent == "show_categories" and matched_categories:
#             # matched_categories = all_categories
#             categories = all_categories
#             for cat in matched_categories:
#                 products.extend(cat.get("products", []))
#             llama_answer = "Here are our categories"

#         # Case: show products with matched categories
#         elif intent == "show_products" and matched_categories:
#             products = []
#             for cat in matched_categories:
#                 products.extend(cat.get("products", []))
#             llama_answer = "Here are the products from your selected categories"


#         elif (intent == "show_products" and not matched_categories) or (intent == "show_categories" and not matched_categories):

#             matched_categories = all_categories
#             for cat in all_categories:
#                 products.extend(cat.get("products", []))
#             llama_answer = "Here are our categories"


#         elif matched_categories == []:
#             llama_answer = "Sorry We do not have such products"


#         # Final fallback: LLaMA answer
#         if not llama_answer:
#             llama_answer = "Here are all our categories."

#         # Save conversation
#         msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
#         r.rpush(session_key, json.dumps(msg))
#         r.ltrim(session_key, -MAX_PREV_CTX, -1)

#         return jsonify({
#             "llama_answer": llama_answer,
#             "categories": matched_categories or None,
#             "products": products or None,
#             "ready_to_call_api": bool(products or matched_categories)
#         }), 200

#     except Exception as e:
#         print(f"[outlet_catalog ERROR] {e}")
#         return jsonify({"error": str(e)}), 500

from utils.ollama_helper import query_ollama,query_ollama_confirmation,query_ollama_quantity, query_ollama_color, query_ollama_size, query_ollama_name, query_ollama_phone, query_ollama_address, query_ollama_image_text_intent, query_ollama_confirmation_order
from sentence_transformers import SentenceTransformer, util

from insta_routes.insta_receive import PRODUCT_ATTRIBUTES_API_URL,fallback_category_match, PRODUCT_VARIANT_CHECK_API_URL, PRODUCT_SIZES_BY_COLOR_API_URL, PRODUCT_ATTRIBUTES_BY_NAME_API_URL, PRODUCT_STOCK_BY_NAME_API_URL, NEGATIVE_SALES_API_URL, get_product_by_name, decimal_default

def handle_color_selection(user_id, product_name, selected_color, state):
    # 1️⃣ Check if product variant with this color exists
    variant_resp = requests.post(
        PRODUCT_VARIANT_CHECK_API_URL,
        json={"title": product_name, "color": selected_color},
        timeout=5
    )
    variant_data = variant_resp.json()

    if not variant_data.get("exists"):
        return {
            "step": "awaiting_color",
            "message": f"❌ The color '{selected_color}' is not available. Please choose another color."
        }

    # 2️⃣ Fetch sizes for this color
    size_resp = requests.post(
        PRODUCT_SIZES_BY_COLOR_API_URL,
        json={"title": product_name, "color": selected_color},
        timeout=5
    )
    size_data = size_resp.json()
    sizes = size_data.get("available_sizes", [])

    # 3️⃣ No sizes at all → skip to quantity
    if not sizes:
        r.set(f"user_state:{user_id}", json.dumps({
            "step": "awaiting_quantity",
            "product_name": product_name,
            "color": selected_color,
            "size": "",
            "price": state["price"],
        }))
        return {
            "step": "awaiting_quantity",
            "message": f"Great! Color '{selected_color}' selected. Please enter quantity."
        }

    # 4️⃣ Only one size → ask confirmation
    if len(sizes) == 1:
        only_size = sizes[0]

        r.set(
            f"user_state:{user_id}",
            json.dumps({
                "step": "awaiting_confirm_single_size",
                "product_name": product_name,
                "color": selected_color,
                "size": only_size,
                "price": state["price"],
            })
        )

        return {
            "step": "awaiting_confirm_single_size",
            "message": f"👕 For '{selected_color}', only size '{only_size}' is available.\nReply YES or NO."
        }

    # 5️⃣ Multiple sizes → ask user
    r.set(
        f"user_state:{user_id}",
        json.dumps({
            "step": "awaiting_size",
            "product_name": product_name,
            "color": selected_color,
            "price": state["price"],
        })
    )

    size_list = "\n".join(f" - {s}" for s in sizes)

    return {
        "step": "awaiting_size",
        "message": f"📏 Available sizes for '{selected_color}':\n{size_list}\n\nPlease reply with your desired size."
    }


import requests
from flask import jsonify

def match_product_response(embedding, user_id):
    url = "https://vibezdc.silverlinepos.com/api/products/match-image/"
    resp = requests.post(url, json={"embedding": embedding}, timeout=10)

    if resp.status_code != 200:
        return jsonify({"error": "Image match API failed"}), 500

    data = resp.json()

    if not data.get("product_name"):
        return jsonify({"matched": False, "message": "No matching product found"}), 200

    product = {}
    if data.get("product_name"):
        product = {
            "title": data["product_name"],
            "price": data["price"],
            "image": data["image_url"],  # <-- Ensure API returns this
            "is_promo" : data ["is_promo"],
            "promo_price": data["promo_price"]
        }

    r.setex(
        f"user:{user_id}:pending_action",
        60,  # 1 hour expiry
        json.dumps({
            "action": "confirm_product_order",
            "product": product  # store entire product dict
        })
    )

    print("state has been changed to pending_action")
    return {
        "matched": True,
        "product": {
            "title": data["product_name"],
            "price": data["price"],
            "image": data["image_url"],
            "is_promo": data["is_promo"],
            "promo_price": data["promo_price"],
            "payload" : f"ORDER_{data["product_name"].upper().replace(' ', '_')}",
            "id" : data.get("product_id", None)
        }
    }

SESSION_TIMEOUT = 3600  # 1 hour
from io import BytesIO

import torch
@app.route("/outlet-catalog", methods=["POST"])
def outlet_catalog():
    try:
        # data = request.get_json(force=True)
        # document_outlet_name = data.get("document_outlet_name")
        # user_id = data.get("user_id")
        # question = data.get("question", "").strip()


        document_outlet_name = request.form.get("document_outlet_name")
        user_id = request.form.get("user_id")
        question = request.form.get("question", "").strip()

        # Image file (optional)
        file = request.files.get("file")

        if not document_outlet_name or not user_id:
            return jsonify({"error": "document_outlet_name and user_id are required"}), 400

        # ==================================================
        # 1️⃣ If IMAGE uploaded → process image first
        # ==================================================
        # if file:
        #     from insta_routes.image import generate_embedding
        #     from insta_routes.image import match_product_response  # or similar function

        #     image_bytes = BytesIO(file.read())
        #     embedding = generate_embedding(image_bytes)
            
        #     intent = None
        #     if question and question != "":
        #         intent = query_ollama_image_text_intent(question)
        #         # return jsonify({"result": intent}), 200
        #     # Call your existing match API
        #     response = match_product_response(embedding)

        #     response["intent"] = intent
            

        #     product_title = response["product"]["title"]
        #     user_query_response = "unknown"
        #     if intent == "identify_product":
        #         user_query_response = "The image you passed matches with this product. Here are its details"
        #     elif intent == "ask_color_options":
        #         user_query_response = "We have following colors available for this product. If the color matches your requirement you can go ahead and place your order."
        #     elif intent == "ask_size_options":
        #         user_query_response = "We have following sizes available for this product. If the size matches your requirement you can go ahead and place your order."
        #     elif intent == "price_query":
        #         user_query_response = "The match for your image found. The price for the product you are looking for can be seen below."
        #     else:
        #         user_query_response = "unknown"
        #     response["user_query_response"] = user_query_response
        #     return jsonify(response), 200

        if file:
            from insta_routes.image import generate_embedding

            image_bytes = BytesIO(file.read())
            embedding = generate_embedding(image_bytes)
            
            intent = None
            if question and question != "":
                intent = query_ollama_image_text_intent(question)

            # Match product from image
            response = match_product_response(embedding, user_id)

            response["intent"] = intent

            product_title = response["product"]["title"]
            price = response["product"]["price"]

            # =====================================================
            #  Fetch Product Attributes (color, size…)
            # =====================================================
            # PRODUCT_ATTRIBUTES_API_URL = "https://vibezdc.silverlinepos.com/api/products/attributes/name"

            try:
                attr_resp = requests.post(
                    PRODUCT_ATTRIBUTES_API_URL,
                    json={"product_name": product_title},
                    timeout=5
                )
                attr_data = attr_resp.json()

                attributes = attr_data.get("attributes", {})
                available_colors = attributes.get("color", [])
                available_sizes = attributes.get("size", [])

                # Add to response so frontend can use it
                response["product"]["attributes"] = {
                    "colors": available_colors,
                    "sizes": available_sizes
                }

            except Exception as e:
                response["product"]["attributes"] = {"error": "Failed to fetch attributes"}

            # ======================================================
            #  Build user-friendly message depending on intent
            # ======================================================
            if intent == "identify_product":
                user_query_response = "The image you passed matches with this product. Here are its details"
            
            elif intent == "ask_color_options":
                user_query_response = (
                    "Here are the available colors for this product: "
                    + ", ".join(available_colors) if available_colors else
                    "Sorry, no color variations were found for this product."
                )

            elif intent == "ask_size_options":
                user_query_response = (
                    "Here are the available sizes for this product: "
                    + ", ".join(available_sizes) if available_sizes else
                    "Sorry, no size variations were found for this product."
                )

            elif intent == "price_query":
                user_query_response = (
                    "You can see the pricing details below."
                )
            elif intent == "order_intent":
                # user_query_response = (
                #     "Great You can click on order to start your order or "
                # )

                # 🔹 Fetch product attributes if any
                attr_resp = requests.post(
                    PRODUCT_ATTRIBUTES_API_URL,
                            json={"product_name": product_title},
                            timeout=5
                        )
                attr_data = attr_resp.json() if attr_resp.status_code == 200 else {}
                attributes = attr_data.get("attributes", {})

                color_options = attributes.get("color", [])
                size_options = attributes.get("size", [])


                if color_options:

                            # ==========================
                            # If only one color exists
                            # ==========================
                    if len(color_options) == 1:
                        only_color = color_options[0]

                        r.set(
                            f"user_state:{user_id}",
                            json.dumps({
                                "step": "awaiting_confirm_single_color",
                                "product_name": product_title,
                                "color": only_color,
                                "price": price
                            }),
                            ex=SESSION_TIMEOUT
                        )

                        message_text = (
                            f"🎨 For *{product_title}*, only color **{only_color}** is available.\n"
                            f"Would you like to continue with this color?\n\n"
                            f"Reply YES or NO."
                        )

                        return jsonify({
                            "success": True,
                            "step": "awaiting_confirm_single_color",
                            "llama_answer": message_text,
                            "categories": [],
                            "products": [],
                            "intent": "place_order"
                        }), 200

                    # ==========================
                    # Multiple colors → normal flow
                    # ==========================
                    r.set(
                        f"user_state:{user_id}",
                        json.dumps({
                            "step": "awaiting_color",
                            "product_name": product_title,
                            "price": price
                            }),
                        ex=SESSION_TIMEOUT
                    )

                    color_list = "\n".join(f"• {c.capitalize()}" for c in color_options)
                    message_text = (
                        f"🛍️ Great choice! You selected *{product_title}*.\n\n"
                        f"🎨 Available colors:\n{color_list}\n\n"
                        f"👉 Please reply with your preferred color name to continue."
                    )

                    return jsonify({
                                "success": True,
                                "step": "awaiting_color",
                                "llama_answer": message_text,
                                "categories": [],
                                "products": [],
                                "intent": "place_order",
                                "chosen_product": {
                                    "name": product_title,
                                    "price": price
                                },
                                "available_colors": color_list,
                            }), 200

                elif size_options:

                            # ==========================
                            # If only one size exists
                            # ==========================
                    if len(size_options) == 1:
                        only_size = size_options[0]

                        r.set(
                            f"user_state:{user_id}",
                            json.dumps({
                                "step": "awaiting_confirm_single_size",
                                "product_name": product_title,
                                "color": "",
                                "size": only_size,
                                "price": price
                            }),
                            ex=SESSION_TIMEOUT
                        )

                        message_text = (
                            f"📏 For *{product_title}*, only size **{only_size}** is available.\n"
                            f"Would you like to continue with this size?\n\n"
                            f"Reply YES or NO."
                        )

                        return jsonify({
                            "success": True,
                            "step": "awaiting_confirm_single_size",
                            "llama_answer": message_text,
                            "categories": [],
                            "products": [],
                            "intent": "place_order"
                        }), 200

                            # ==========================
                            # Multiple sizes → normal flow
                            # ==========================
                    r.set(
                        f"user_state:{user_id}",
                        json.dumps({
                            "step": "awaiting_size",
                            "product_name": product_title,
                            "price": price
                        }),
                        ex=SESSION_TIMEOUT
                    )

                    size_list = "\n".join(f"• {s.capitalize()}" for s in size_options)
                    message_text = (
                        f"🛍️ Great choice! You selected *{product_title}*.\n\n"
                        f"📏 Available sizes:\n{size_list}\n\n"
                        f"👉 Please reply with your preferred size to continue."
                    )

                    return jsonify({
                        "success": True,
                        "step": "awaiting_size",
                        "llama_answer": message_text,
                        "product": {
                            "name": product_title,
                            "price": price
                        },
                        "available_sizes": size_list,
                        "categories": [],
                        "products": [],
                        "intent": "place_order"
                    }), 200



                else:
                    r.set(f"user_state:{user_id}", json.dumps({
                                "step": "awaiting_quantity",
                                "product_name": product_title,
                                "price": price
                            }),ex=SESSION_TIMEOUT)

                    message_text = (
                                f"🛍️ Great choice! You selected *{product_title}*.\n\n"
                                f"🧮 Please reply with the quantity you'd like to order.\n"
                                f"👉 Example: `1`, `2`, or `3`"
                            )
                    return jsonify({
                        "success": True,
                        "step": "awaiting_quantity",
                        "llama_answer": message_text,
                        "product": {
                            "name": product_title,
                            "price": price
                        },
                        "products": [],
                        "categories": [],
                        "intent" : "place_order"
                    }), 200

            else:
                user_query_response = "The image you passed matches this product. Is this what you are looking for ?"

            response["llama_answer"] = user_query_response + " Would you like to order this?"

            return jsonify(response), 200

        quick_reply = None  
        qr_raw = request.form.get("quick_reply", None)

        if qr_raw:
            try:
                quick_reply = json.loads(qr_raw)
            except:
                return jsonify({"error": "Invalid quick_reply JSON"}), 400

        if not document_outlet_name or not user_id:
            return jsonify({"error": "document_outlet_name and user_id are required"}), 400

        session_key = f"session:{document_outlet_name}:{user_id}"
        session_state_key = f"state:{document_outlet_name}:{user_id}"

        previous_msgs = r.lrange(session_key, -MAX_PREV_CTX, -1) or []
        previous_context = " ".join(
            [(json.loads(m)["question"] + " " + json.loads(m)["answer"]) for m in previous_msgs if m]
        )

        knowledge_context = ""

        context = ""

        # Fetch category API
        category_resp = requests.get(CATEGORIES_API_URL, timeout=5)
        categories = category_resp.json() if category_resp.status_code == 200 else []

        PRODUCTS_LIST = []
        for cat in categories:
            for p in cat.get("products", []):
                PRODUCTS_LIST.append(p["title"])

        llama_intent_raw = query_ollama(question, context, product_titles=PRODUCTS_LIST)
        llama_intent = llama_intent_raw
        # llama_intent = json.loads(llama_intent_raw)
        intent = "could not understand"


        

        intent = llama_intent.get("intent", "none")
        # --- Mark this message as processed ---
        # save_processed_message(sender_id, message_id, intent)
        category_filter = llama_intent.get("category_filter")
        negative_intent = llama_intent.get("negative_intent", False)
        product_name = llama_intent.get("product_name")

        llama_answer = None

        resp = requests.get(CATEGORIES_API_URL, timeout=5)
        all_categories = resp.json() if resp.status_code == 200 else []

        all_products = []
        for c in all_categories:
            all_products.extend(c.get("products", []))


        # if "quick_reply" in data:
        #     payload = data["quick_reply"]["payload"]
        if quick_reply:
            payload = quick_reply["payload"]

            if payload.startswith("PRODUCT_"):
                product_name = payload.replace("PRODUCT_", "").replace("_", " ")
                print(f"User selected product: {product_name}")

                # 3️⃣ Send product details
                product = get_product_by_name(product_name)  # Fetch from your DB/API
                # send_instagram_product_details(sender_id, product)

            elif payload.startswith("ORDER_"):
                # User clicked "Order" button for a product
                product_name = payload.replace("ORDER_", "").replace("_", " ")
                print(f"User wants to order: {product_name}")

                try:
                    # 1️⃣ Fetch all categories and products from API
                    category_resp = requests.get(
                                CATEGORIES_API_URL,
                                timeout=5
                            )
                    categories = category_resp.json() if category_resp.status_code == 200 else []

                    # 2️⃣ Find the clicked product
                    matched_product = None
                    for cat in categories:
                        for p in cat.get("products", []):
                            if p["title"].lower() == product_name.lower():
                                matched_product = p
                                break
                        if matched_product:
                            break

                    if not matched_product:
                        message_text = f"❌ Could not find the product '{product_name}'."
                        return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "place_order",
                            "step" : "product_not_found",
                            "llama_answer": message_text
                        }), 200

                    # 3️⃣ Default quantity = 1
                    quantity = 0
                    print("matched product from category api", matched_product)
                    is_promo = matched_product["is_promo"]
                    price = float(matched_product["price"])

                    if is_promo:
                        price = float(matched_product.get("promo_price", 0.0))
                    total_price = price * quantity

                    # 5️⃣ Check if product has attributes
                    attr_resp = requests.post(
                        PRODUCT_ATTRIBUTES_API_URL,
                        json={"product_name": matched_product["title"]},
                        timeout=5
                    )
                    attr_data = attr_resp.json() if attr_resp.status_code == 200 else {}
                    attributes = attr_data.get("attributes", {})

                    color_options = attributes.get("color", [])
                    size_options = attributes.get("size", [])


                    if color_options:

                        # If only ONE color exists → ask for confirmation
                        if len(color_options) == 1:
                            only_color = color_options[0]

                            r.set(
                                f"user_state:{user_id}",
                                json.dumps({
                                    "step": "awaiting_confirm_single_color",
                                    "product_name": matched_product["title"],
                                    "color": only_color,
                                    "price": price
                                }),
                                ex=SESSION_TIMEOUT
                            )

                            message_text = (
                                f"🎨 For *{matched_product['title']}*, only color **{only_color}** is available.\n"
                                f"Would you like to continue with this color?\n\n"
                                f"Reply YES or NO."
                            )

                            r.delete(f"user:{user_id}:pending_action")

                            return jsonify({
                                "success": True,
                                "categories": [],
                                "products": [],
                                "intent": "place_order",
                                "step": "awaiting_confirm_single_color",
                                "llama_answer": message_text
                            }), 200

                        # -----------------------------------------------
                        # If multiple colors exist → normal flow
                        # -----------------------------------------------
                        r.set(
                            f"user_state:{user_id}",
                            json.dumps({
                                "step": "awaiting_color",
                                "product_name": matched_product["title"],
                                "price": price
                            }),
                            ex=SESSION_TIMEOUT
                        )

                        color_list = "\n".join(f"• {c.capitalize()}" for c in color_options)
                        message_text = (
                            f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                            f"🎨 Available colors:\n{color_list}\n\n"
                            f"👉 Please reply with your preferred color to continue."
                        )

                        r.delete(f"user:{user_id}:pending_action")

                        return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent": "place_order",
                            "step": "awaiting_color",
                            "llama_answer": message_text
                        }), 200


                    elif size_options:

                        # If only one size exists → Ask for confirmation
                        if len(size_options) == 1:
                            only_size = size_options[0]

                            r.set(
                                f"user_state:{user_id}",
                                json.dumps({
                                    "step": "awaiting_confirm_single_size",
                                    "product_name": matched_product["title"],
                                    "color": "",
                                    "size": only_size,
                                    "price": price
                                }),
                                ex=SESSION_TIMEOUT
                            )

                            message_text = (
                                f"📢 For *{matched_product['title']}*, only size **{only_size}** is available.\n"
                                f"Would you like to continue with this size?\n\n"
                                f"Reply YES or NO."
                            )

                            r.delete(f"user:{user_id}:pending_action")

                            return jsonify({
                                "success": True,
                                "categories": [],
                                "products": [],
                                "intent": "place_order",
                                "step": "awaiting_confirm_single_size",
                                "llama_answer": message_text
                            }), 200

                        # -------------------------------
                        # If multiple sizes exist → normal
                        # -------------------------------
                        r.set(
                            f"user_state:{user_id}",
                            json.dumps({
                                "step": "awaiting_size",
                                "product_name": matched_product["title"],
                                "price": price
                            }),
                            ex=SESSION_TIMEOUT                       
                        )

                        size_list = "\n".join(f"• {s.capitalize()}" for s in size_options)
                        message_text = (
                            f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                            f"📏 Available sizes:\n{size_list}\n\n"
                            f"👉 Please reply with your preferred size to continue."
                        )

                        r.delete(f"user:{user_id}:pending_action")

                        return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent": "place_order",
                            "step": "awaiting_size",
                            "llama_answer": message_text
                        }), 200

                    else:
                        r.set(f"user_state:{user_id}", json.dumps({
                                    "step": "awaiting_quantity",
                                    "product_name": matched_product["title"],
                                    "price": price
                                }),ex=SESSION_TIMEOUT)

                        message_text = (
                                    f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                    f"🧮 Please reply with the quantity you'd like to order.\n"
                                    f"👉 Example: `1`, `2`, or `3`"
                                )
                        r.delete(f"user:{user_id}:pending_action")
                            
                        # send_instagram_message(sender_id, message_text)
                        return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "place_order",
                            "step" : "awaiting_quantity",
                            "llama_answer": message_text
                        }), 200
                                

                except Exception as e:
                    r.delete(f"user:{user_id}:pending_action")

                    print("⚠️ Failed to process quick reply order:", e)
                    # send_instagram_message(sender_id, "❌ Something went wrong while processing your order. Please try again.")
                        # send_instagram_message(sender_id, message_text)
                    message_text = "Something went wrong while processing your order. Please try again."
                    return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "place_order",
                            "step" : "",
                            "llama_answer": message_text
                        }), 200

            # ---------- CATEGORY SELECTION ----------
            elif payload.startswith("CATEGORY_"):
                category_name = payload.replace("CATEGORY_", "").replace("_", " ")
                print(f"User selected category: {category_name}")

                # Fetch products for this category
                resp = requests.get(CATEGORIES_API_URL, timeout=5)
                all_categories = resp.json() if resp.status_code == 200 else []

                selected_category = next((c for c in all_categories if c['title'].lower() == category_name.lower()), None)

                if selected_category:
                    products_to_send = selected_category.get("products", [])
                    if products_to_send:
                        # Send as carousel
                        # send_instagram_carousel_initial(sender_id, products_to_send)
                        message_text = "Here are the products of selected category"
                        return jsonify({
                                "success": True,
                                "categories": [],
                                "products": products_to_send,
                                "intent" : "place_order",
                                "step" : "",
                                "llama_answer": message_text
                            }), 200

                        # # Optionally store products globally for session
                        # global PRODUCTS
                        # PRODUCTS = {p['title'].lower(): p for p in products_to_send}
                    else:
                        message_text = f"No products found in category '{category_name}'."
                        return jsonify({
                                "success": True,
                                "categories": [],
                                "products": products_to_send,
                                "intent" : "place_order",
                                "step" : "",
                                "llama_answer": message_text
                            }), 200
                else:
                    message_text = f"Category '{category_name}' not found."
                    return jsonify({
                                "success": True,
                                "categories": [],
                                "products": products_to_send,
                                "intent" : "place_order",
                                "step" : "",
                                "llama_answer": message_text
                            }), 200


        attribute_state_json = r.get(f"user_state:{user_id}")
        if attribute_state_json:
            state = json.loads(attribute_state_json)
            step = state.get("step")

            if step == "awaiting_confirm_single_size":
                user_answer = question.lower().strip()
                confirm_intent = query_ollama_confirmation(user_answer)
                if confirm_intent == "confirm_yes":
                # if user_answer in ("yes", "y", "ok", "sure"):
                    product_name = state["product_name"]
                    selected_color = state["color"]
                    selected_size = state["size"]

                    # Move the user directly to awaiting_quantity
                    r.set(
                        f"user_state:{user_id}",
                        json.dumps({
                            "step": "awaiting_quantity",
                            "product_name": product_name,
                            "color": selected_color,
                            "size": selected_size,
                            "price": state["price"],
                        }),
                        ex=SESSION_TIMEOUT
                    )

                    message_text = (
                        f"📝 Great! You selected {selected_color} (size {selected_size}).\n"
                        f"Please enter quantity."
                    )

                    return jsonify({
                        "success": True,
                        "categories": [],
                        "products": [],
                        "intent": "place_order",
                        "step": "awaiting_quantity",
                        "llama_answer": message_text
                    }), 200

                else:
                    # If NO → Go back (maybe ask user to pick color again or cancel)
                    r.delete(f"user_state:{user_id}")
                    return jsonify({
                        "success": True,
                        "categories": [],
                        "products": [],
                        "intent": "place_order",
                        # "step": state.get("previous_step", "awaiting_color"),
                        "llama_answer": f"Thank you. That size is currently unavailable for this product. We will certainly consider this option for future inventory."
                    }), 200 

            elif step == "awaiting_confirm_single_color":
                user_answer = question.lower().strip()
                confirm_intent = query_ollama_confirmation(user_answer)
                if confirm_intent == "confirm_yes":
                # if user_answer in ("yes", "y", "ok", "sure"):
                    selected_color = state["color"]
                    product_name = state["product_name"]

                    resp = handle_color_selection(user_id, product_name, selected_color, state)

                    return jsonify({
                        "success": True,
                        "categories": [],
                        "products": [],
                        "intent": "place_order",
                        "step": resp["step"],
                        "llama_answer": resp["message"]
                    }), 200

                else:
                    # If NO → Go back (maybe ask user to pick color again or cancel)
                    r.delete(f"user_state:{user_id}")
                    return jsonify({
                        "success": True,
                        "categories": [],
                        "products": [],
                        "intent": "place_order",
                        # "step": state.get("previous_step", "awaiting_color"),
                        "llama_answer": f"Thank you. The color '{state['color']}' is currently unavailable for this product. We will certainly consider this option for future inventory."
                    }), 200 

            elif step == "awaiting_color":

                selected_color = query_ollama_color(question)
                product_name = state["product_name"]

                # ✅ Call your exact-match variant check API
                variant_resp = requests.post(
                            PRODUCT_VARIANT_CHECK_API_URL,  # /api/productvariantcheck/
                            json={"title": product_name, "color": selected_color},
                            timeout=5
                        )
                variant_data = variant_resp.json()

                if variant_data.get("exists"):

                    resp = handle_color_selection(user_id, product_name, selected_color, state)

                    return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "place_order",
                            "step" : resp["step"],
                            "llama_answer": resp["message"]
                        }), 200

                else:

                    # ✅ Fetch all valid colors for the product
                    attributes_resp = requests.post(
                                PRODUCT_ATTRIBUTES_BY_NAME_API_URL,  # /api/products/attributes/name
                                json={"product_name": product_name},
                                timeout=5
                            )
                    attributes_data = attributes_resp.json()
                    available_colors = attributes_data.get("attributes", {}).get("color", [])

                    if available_colors:
                        message_text = f"❌ The color '{selected_color}' is not available for '{product_name}'." + "Available colors:\n" +"\n".join(f"   - {c}" for c in available_colors)
                        return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "place_order",
                            "step" : "awaiting_color",
                            "llama_answer": message_text
                        }), 200                               
                    else:
                        message_text = "No colors are currently available for this product."
                        return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "place_order",
                            "step" : "awaiting_color",
                            "llama_answer": message_text
                        }), 200         
                        

            elif step == "awaiting_size":
                # selected_size = text.strip().upper()
                selected_size = query_ollama_size(question)

                product_name = state["product_name"]
                selected_color = state.get("color", "")

                # ✅ Call your exact-match variant check API with both color and size
                variant_resp = requests.post(
                            PRODUCT_VARIANT_CHECK_API_URL,
                            json={
                                "title": product_name,
                                "color": selected_color,
                                "size": selected_size
                            },
                            timeout=5
                        )
                variant_data = variant_resp.json()

                if variant_data.get("exists"):
                    # ✅ Valid color+size → move to quantity
                    r.set(f"user_state:{user_id}", json.dumps({
                                "step": "awaiting_quantity",
                                "product_name": product_name,
                                "color": selected_color,
                                "size": selected_size,
                                "price": state["price"],
                            }),ex=SESSION_TIMEOUT)
                    message_text = f"📝 Great! You chose {selected_size} in {selected_color}.\nPlease enter quantity."
                    return jsonify({
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "place_order",
                            "step" : "awaiting_quantity",
                            "llama_answer": message_text
                        }), 200  
                    
                else:
                    # ❌ Invalid size → stay in same step and show all available sizes for this color
                    size_resp = requests.post(
                                PRODUCT_SIZES_BY_COLOR_API_URL,
                                json={"title": product_name, "color": selected_color},
                                timeout=5
                            )
                    size_data = size_resp.json()
                    available_sizes = size_data.get("available_sizes", [])

                    if available_sizes:

                        message_text = f"❌ The size '{selected_size}' is not available for '{product_name}' in {selected_color}."+"Available sizes:\n" + "\n".join(f"   - {s}" for s in available_sizes)
                        return jsonify({
                                "success": True,
                                "categories": [],
                                "products": [],
                                "intent" : "place_order",
                                "step" : "awaiting_size",
                                "llama_answer": message_text
                            }), 200  

                    else:
                        # send_instagram_message(sender_id,
                        #             f"No sizes are currently available for color '{selected_color}'."
                        #         )
                        message_text = f"No sizes are currently available for color '{selected_color}'."
                        return jsonify({
                                "success": True,
                                "categories": [],
                                "products": [],
                                "intent" : "place_order",
                                "step" : "awaiting_size",
                                "llama_answer": message_text
                            }), 200  

            elif step == "awaiting_quantity":
                qty = query_ollama_quantity(question)
                if qty <= 0:
                    # send_instagram_message(sender_id, "❌ Please enter a valid quantity (like 1, 2, or 3).")
                    # return
                    message_text = "❌ Please enter a valid quantity (like 1, 2, or 3)."
                    return jsonify({
                                "success": True,
                                "categories": [],
                                "products": [],
                                "intent" : "place_order",
                                "step" : "awaiting_quantity",
                                "llama_answer": message_text
                            }), 200  

                product_name = state["product_name"]
                color = state.get("color", "")
                size = state.get("size", "")
                price = state["price"]

                # ✅ Step 1: Check stock using your stock API
                try:
                    stock_resp = requests.post(
                        PRODUCT_STOCK_BY_NAME_API_URL, 
                        json={
                                    "product_name": product_name,
                                    "color": color or "",
                                    "size": size or "",
                                    "gender": "",
                                    "style": "",
                                    "fit": "",
                                    "season": ""
                                },
                                timeout=5
                            )
                    stock_data = stock_resp.json()
                    available_stock = stock_data.get("stock_quantity", 0)
                    product_has_attributes = stock_data.get("product_has_attributes", False)
                except Exception as e:
                    print("Error checking stock:", e)
                    # send_instagram_message(sender_id, "⚠️ Couldn’t check stock right now. Please try again later.")
                    message_text = "⚠️ Couldn’t check stock right now. Please try again later."
                    return jsonify({
                                "success": True,
                                "categories": [],
                                "products": [],
                                "intent" : "place_order",
                                "step" : "check_stock",
                                "llama_answer": message_text
                            }), 200 

                        # ✅ Step 2: Handle product with or without attributes separately
                if product_has_attributes:
                            # Product has defined attributes in DB — must obey stock limits

                    negative_sale_resp = requests.get(
                                NEGATIVE_SALES_API_URL, 
                                timeout=5
                            )
                    neg_sales_data = negative_sale_resp.json()
                    allow_negative_sales = neg_sales_data.get("allow_negative_sales", False)
                            # product_has_attributes = stock_data.get("product_has_attributes", False)

                    if allow_negative_sales == False:
                        if available_stock <= 0:
                            # send_instagram_message(
                            #             sender_id,
                            #             f"❌ Sorry, '{product_name}' ({color or 'N/A'}, {size or 'N/A'}) is currently out of stock."
                            #         )

                            r.delete(f"user_state:{user_id}")
                            message_text = f"❌ Sorry, '{product_name}' ({color or 'N/A'}, {size or 'N/A'}) is currently out of stock."
                            return jsonify({
                                        "success": True,
                                        "categories": [],
                                        "products": [],
                                        "intent" : "place_order",
                                        "step" : "check_stock",
                                        "llama_answer": message_text
                                    }), 200 

                        if qty > available_stock:

                            message_text = f"⚠️ Only {available_stock} items available for '{product_name}' in {color or 'N/A'} {size or 'N/A'}.\n"+ f"Please enter a smaller quantity."
                            return jsonify({
                                        "success": True,
                                        "categories": [],
                                        "products": [],
                                        "intent" : "place_order",
                                        "step" : "awaiting_quantity",
                                        "llama_answer": message_text
                                    }), 200 
                    else:
                        pass

                else:
                    pass

                r.delete(f"user_state:{user_id}")

                
                message_text = f"✅ You selected:\n" + f"Product: {product_name}\n"+f"Color: {color or 'N/A'}\n"+f"Size: {size or 'N/A'}\n"+f"Quantity: {qty}\n\n"+"Your order is placed in cart. 🛒 Feel free to continue shopping. Let me know when you want to checkout!"
                product_detail = get_product_by_name(product_name)
                return jsonify({                            
                            "success": True,
                            "categories": [],
                            "products": [],
                            "intent" : "add_to_cart",
                            "step" : "add_to_cart",
                            "cart_item": {
                                "product" : product_name,
                                "id": product_detail["id"],
                                "image": product_detail["image"],
                                "promo_price": product_detail["promo_price"],
                                "is_promo": product_detail["is_promo"],
                                "color": color,
                                "size" : size,
                                "qty" : qty,
                                "price" : product_detail["price"],
                                "unit" : product_detail["unit"],
                            },
                            "llama_answer": message_text
                            }), 200 
        pending_action = False
        if r.get(f"user:{user_id}:pending_action"):
            pending_raw = r.get(f"user:{user_id}:pending_action")
            print("I am inside pending raw")
            pending = json.loads(pending_raw)
            action = pending.get("action")
            product = pending.get("product")

            print("text_lower in pending action", question)

            print("action from pending", action)

            # 2️⃣ If bot was expecting product confirmation
            if action == "confirm_product_order":

                confirm_intent = query_ollama_confirmation_order(question)
                # if text_lower in AFFIRMATIVE:
                if confirm_intent == "confirm_yes":
                    print("I am inside pending raw affirmative")
                    # ✅ Convert to place_order intent
                    r.delete(f"user:{user_id}:pending_action")

                    llama_intent = {
                                        "intent": "place_order",
                                        "category_filter": None,
                                        "order_items": [
                                            {"product": product["title"], "quantity": 0, "color": None, "size": None,
                                            "gender": None, "style": None, "season": None, "fit": None,
                                            "missing_slots": []}
                                        ],
                                        "customer_details": {"name": None, "address": None, "phone": None},
                                        "negative_intent": False
                                    }
                    intent = "place_order"
                    pending_action = True

                elif confirm_intent == "confirm_no":
                    print("I am inside pending raw negative")
                    r.delete(f"user:{user_id}:pending_action")
                    # return {"intent": "cancel_order"}
                    llama_intent = {
                                        "intent": "cancel_order"
                                    }
                    intent = "cancel_order"
                    pending_action = True

                else:
                    # user typed something else → ask again
                    # send_instagram_message(user_id, "I couldn't understand you .Please reply yes or no.")
                    return jsonify({
                        "success": True,
                        "categories": [],
                        "products": [],
                        "intent" : "show_categories",
                        "llama_answer": "Here are all our categories",
                        "step" : ""
                    }), 200
                    pending_action = True
                    # continue


        # --- Handle category or product intent ---
        if intent == "show_categories":
            return jsonify({
                "success": True,
                "categories": all_categories,
                "products": all_products,
                "intent" : "show_categories",
                "llama_answer": "Here are all our categories",
                "step" : ""
            }), 200

        elif intent == "show_products" and not category_filter:
            return jsonify({
                "success": True,
                "categories": all_categories,
                "products": all_products,
                "intent" : "show_products",
                "llama_answer": "Here are all our products",
                "step": ""

            }), 200
        elif intent == "show_products" and category_filter:
            selected_category = next((c for c in all_categories if category_filter.lower() in c['title'].lower()), None)
            if selected_category:
                products_to_send = selected_category.get("products", [])
                if products_to_send:
                    return jsonify({
                        "success": True,
                        "categories" : selected_category,
                        "products": products_to_send,
                        "intent" : "show_products",
                        "llama_answer": f"Here are all our products of {category_filter}",
                        "step" : ""

                    }), 200                    

        elif intent == "place_order":
            print("inside place order from yes")
            order_items = llama_intent.get("order_items", [])

            print("order items", order_items)
            if order_items and len(order_items) > 0:
                try:
                    # 🔹 Fetch all categories and their products
                    category_resp = requests.get(
                                CATEGORIES_API_URL,
                                timeout=5
                            )
                    categories = category_resp.json() if category_resp.status_code == 200 else []


                    print("len", len(order_items))
                    #   Flatten products into dict and list for embeddings
                    PRODUCTS_LOOKUP = {}
                    PRODUCTS_LIST = []
                    for cat in categories:
                        for p in cat.get("products", []):
                            PRODUCTS_LIST.append(p["title"])
                            PRODUCTS_LOOKUP[p["title"]] = p
                    # Create embeddings for all products (you can cache this for performance)
                    PRODUCT_EMBEDS = embedder.encode(PRODUCTS_LIST, convert_to_tensor=True)

                    # for item in order_items:
                    #     product_name_input = item.get("product")
                    #     quantity = item.get("quantity") or 0

                    #     if not product_name_input:
                    #         continue

                    #     # Embed user text
                    #     user_embed = embedder.encode(product_name_input, convert_to_tensor=True)
                    #     cos_scores = util.cos_sim(user_embed, PRODUCT_EMBEDS)[0]
                    #     cos_scores_list = cos_scores.tolist()

                    #     # Top 5 sorted indices
                    #     top_indices = sorted(
                    #         range(len(cos_scores_list)),
                    #         key=lambda i: cos_scores_list[i],
                    #         reverse=True
                    #     )[:5]

                    #     top_scores = [cos_scores_list[i] for i in top_indices]

                    #     top1 = top_scores[0]
                    #     top2 = top_scores[1] if len(top_scores) > 1 else 0

                    #     similarity_gap = top1 - top2

                    #     print("Top scores:", top_scores)
                    #     print("Similarity GAP:", similarity_gap)

                    #     MIN_SIMILARITY = 0.55     # prevent low-confidence matches
                    #     GAP_THRESHOLD = 0.24      # your existing logic

                    #     # Case A — Clear strong match
                    #     if top1 >= MIN_SIMILARITY and similarity_gap >= GAP_THRESHOLD:
                    #         matched_product_name = PRODUCTS_LIST[top_indices[0]]
                    #         matched_product = PRODUCTS_LOOKUP[matched_product_name]

                    #     # Case B — Ambiguous → return similar products list
                    #     else:
                    #         similar_products = []
                    #         for idx in top_indices:
                    #             candidate_name = PRODUCTS_LIST[idx]
                    #             product_data = PRODUCTS_LOOKUP[candidate_name]

                    #             similar_products.append({
                    #                 "name": product_data.get("title"),
                    #                 "price": product_data.get("promo_price") if product_data.get("is_promo") else product_data.get("price"),
                    #                 "image": product_data.get("image"),
                    #                 "description": product_data.get("description"),
                    #                 "is_promo": product_data.get("is_promo"),
                    #                 "promo_price": product_data.get("promo_price"),
                    #                 "unit": product_data.get("unit"),
                    #                 "payload": f"ORDER_{candidate_name.upper().replace(' ', '_')}"
                    #             })

                    #         return jsonify({
                    #             "success": True,
                    #             "llama_answer": f"Multiple products found similar to '{product_name_input}'.",
                    #             "products": similar_products,
                    #             "categories": [],
                    #             "intent": "place_order",
                    #             "step": "product_selection_confusion"
                    #         }), 200

                    for item in order_items:
                        product_name_input = item.get("product")
                        quantity = item.get("quantity") or 0

                        if not product_name_input:
                            continue

                        # Embed user text
                        user_embed = embedder.encode(product_name_input, convert_to_tensor=True)
                        cos_scores = util.cos_sim(user_embed, PRODUCT_EMBEDS)[0]
                        cos_scores_list = cos_scores.tolist()

                        # Top 5 sorted indices
                        top_indices = sorted(
                            range(len(cos_scores_list)),
                            key=lambda i: cos_scores_list[i],
                            reverse=True
                        )[:5]

                        top_scores = [cos_scores_list[i] for i in top_indices]

                        # 🔍 **Print matched products + scores**
                        print("\n🔍 Similarity Results for:", product_name_input)
                        for rank, (idx, score) in enumerate(zip(top_indices, top_scores), start=1):
                            print(f"  {rank}. {PRODUCTS_LIST[idx]} → similarity: {score:.4f}")

                        top1 = top_scores[0]
                        top2 = top_scores[1] if len(top_scores) > 1 else 0

                        similarity_gap = top1 - top2

                        print("Top scores:", top_scores)
                        print("Similarity GAP:", similarity_gap)

                        MIN_SIMILARITY = 0.60     # prevent low-confidence matches
                        GAP_THRESHOLD = 0.20      # your existing logic

                        # Case A — Clear strong match
                        if top1 >= MIN_SIMILARITY and similarity_gap >= GAP_THRESHOLD:
                            matched_product_name = PRODUCTS_LIST[top_indices[0]]
                            matched_product = PRODUCTS_LOOKUP[matched_product_name]

                        # Case B — Ambiguous → return similar products list
                        else:
                            similar_products = []
                            for idx in top_indices:
                                candidate_name = PRODUCTS_LIST[idx]
                                product_data = PRODUCTS_LOOKUP[candidate_name]

                                similar_products.append({
                                    "name": product_data.get("title"),
                                    "price": product_data.get("promo_price") if product_data.get("is_promo") else product_data.get("price"),
                                    "image": product_data.get("image"),
                                    "description": product_data.get("description"),
                                    "is_promo": product_data.get("is_promo"),
                                    "promo_price": product_data.get("promo_price"),
                                    "unit": product_data.get("unit"),
                                    "payload": f"ORDER_{candidate_name.upper().replace(' ', '_')}"
                                })

                            return jsonify({
                                "success": True,
                                "llama_answer": f"Multiple products found similar to '{product_name_input}'.",
                                "products": similar_products,
                                "categories": [],
                                "intent": "place_order",
                                "step": "product_selection_confusion"
                            }), 200
                        

                        matched_product = PRODUCTS_LOOKUP[matched_product_name]


                        is_promo = matched_product["is_promo"]
                        price = float(matched_product["price"])

                        if is_promo:
                            price = float(matched_product.get("promo_price", 0.0))
                        # 🔹 Calculate total price
                        total_price = price * int(quantity)

                        # 🔹 Fetch product attributes if any
                        attr_resp = requests.post(
                            PRODUCT_ATTRIBUTES_API_URL,
                                    json={"product_name": matched_product["title"]},
                                    timeout=5
                                )
                        attr_data = attr_resp.json() if attr_resp.status_code == 200 else {}
                        attributes = attr_data.get("attributes", {})

                        color_options = attributes.get("color", [])
                        size_options = attributes.get("size", [])


                        if color_options:

                            # ==========================
                            # If only one color exists
                            # ==========================
                            if len(color_options) == 1:
                                only_color = color_options[0]

                                r.set(
                                    f"user_state:{user_id}",
                                    json.dumps({
                                        "step": "awaiting_confirm_single_color",
                                        "product_name": matched_product["title"],
                                        "color": only_color,
                                        "price": price
                                    }),
                                    ex=SESSION_TIMEOUT
                                )

                                message_text = (
                                    f"🎨 For *{matched_product['title']}*, only color **{only_color}** is available.\n"
                                    f"Would you like to continue with this color?\n\n"
                                    f"Reply YES or NO."
                                )

                                return jsonify({
                                    "success": True,
                                    "step": "awaiting_confirm_single_color",
                                    "llama_answer": message_text,
                                    "categories": [],
                                    "products": [],
                                    "intent": "place_order"
                                }), 200

                            # ==========================
                            # Multiple colors → normal flow
                            # ==========================
                            r.set(
                                f"user_state:{user_id}",
                                json.dumps({
                                    "step": "awaiting_color",
                                    "product_name": matched_product["title"],
                                    "price": price
                                }),
                                ex=SESSION_TIMEOUT

                            )

                            color_list = "\n".join(f"• {c.capitalize()}" for c in color_options)
                            message_text = (
                                f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                f"🎨 Available colors:\n{color_list}\n\n"
                                f"👉 Please reply with your preferred color name to continue."
                            )

                            return jsonify({
                                "success": True,
                                "step": "awaiting_color",
                                "llama_answer": message_text,
                                "categories": [],
                                "products": [],
                                "intent": "place_order",
                                "chosen_product": {
                                    "name": matched_product["title"],
                                    "price": price
                                },
                                "available_colors": color_list,
                            }), 200

                        elif size_options:

                            # ==========================
                            # If only one size exists
                            # ==========================
                            if len(size_options) == 1:
                                only_size = size_options[0]

                                r.set(
                                    f"user_state:{user_id}",
                                    json.dumps({
                                        "step": "awaiting_confirm_single_size",
                                        "product_name": matched_product["title"],
                                        "color": "",
                                        "size": only_size,
                                        "price": price
                                    })
                                )

                                message_text = (
                                    f"📏 For *{matched_product['title']}*, only size **{only_size}** is available.\n"
                                    f"Would you like to continue with this size?\n\n"
                                    f"Reply YES or NO."
                                )

                                return jsonify({
                                    "success": True,
                                    "step": "awaiting_confirm_single_size",
                                    "llama_answer": message_text,
                                    "categories": [],
                                    "products": [],
                                    "intent": "place_order"
                                }), 200

                            # ==========================
                            # Multiple sizes → normal flow
                            # ==========================
                            r.set(
                                f"user_state:{user_id}",
                                json.dumps({
                                    "step": "awaiting_size",
                                    "product_name": matched_product["title"],
                                    "price": price
                                }),
                                ex=SESSION_TIMEOUT
                            )

                            size_list = "\n".join(f"• {s.capitalize()}" for s in size_options)
                            message_text = (
                                f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                f"📏 Available sizes:\n{size_list}\n\n"
                                f"👉 Please reply with your preferred size to continue."
                            )

                            return jsonify({
                                "success": True,
                                "step": "awaiting_size",
                                "llama_answer": message_text,
                                "product": {
                                    "name": matched_product["title"],
                                    "price": price
                                },
                                "available_sizes": size_list,
                                "categories": [],
                                "products": [],
                                "intent": "place_order"
                            }), 200



                        else:
                            r.set(f"user_state:{user_id}", json.dumps({
                                        "step": "awaiting_quantity",
                                        "product_name": matched_product["title"],
                                        "price": price
                                    }), ex=SESSION_TIMEOUT)

                            message_text = (
                                        f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                        f"🧮 Please reply with the quantity you'd like to order.\n"
                                        f"👉 Example: `1`, `2`, or `3`"
                                    )
                            return jsonify({
                                "success": True,
                                "step": "awaiting_quantity",
                                "llama_answer": message_text,
                                "product": {
                                    "name": matched_product["title"],
                                    "price": price
                                },
                                "products": [],
                                "categories": [],
                                "intent" : "place_order"
                            }), 200

                except Exception as e:
                    print("⚠️ Order processing failed:", e)
                    # send_instagram_message(sender_id, "❌ Something went wrong while processing your order. Please try again.")
                    return jsonify({
                                "success": True,
                                "step": "order_failed",
                                "llama_answer": (
                                    f"❌ Something went wrong while processing your order. Please try again.\n"
                                ),
                            }), 200
        elif intent == "check_out":
            print("checkout_intent", intent)
            print("I am in confirm order")
            # Check if the user already has a state in Redis
            state_json = r.get(f"confirm_order:{user_id}")
            # r.delete(f"confirm_order:{sender_id}")

            print("state_json", state_json)
            if not state_json:
                # Initialize multi-step flow in Redis
                state = {
                            "step": "asking_name",
                            "customer_details": {"name": None, "address": None, "phone": None},
                            # "orders": orders,
                            "orders": "",
                            "payment": {"mode": None, "image": None}  # new field
                        }
                # ✅ Use custom converter to handle Decimal
                # r.set(f"confirm_order:{user_id}", json.dumps(state, default=decimal_default))
                # send_instagram_message(user_id, "Sure! Let's confirm your order. Please tell me your full name.")
                message_text = "Sure! Let's confirm your order. Please tell me your full name."
                return jsonify({
                                "success": True,
                                "step": "asking_name",
                                "llama_answer": message_text,
                                "products": [],
                                "categories": [],
                                "intent" : "checkout_order"
                            }), 200
            
        elif intent == "view_cart":
                message_text = "Sure here are items from your cart"
                return jsonify({
                                "success": True,
                                "step": "show_cart_items",
                                "llama_answer": message_text,
                                "products": [],
                                "categories": [],
                                "intent" : "view_cart"
                            }), 200
        # elif intent == "view_clearance_sales":
        #         message_text = "Sure here are items from your clearance sales"
        #         return jsonify({
        #                         "success": True,
        #                         "step": "view_clearance_sales",
        #                         "llama_answer": message_text,
        #                         "products": [],
        #                         "categories": [],
        #                         "intent" : "view_clearance_sales"
        #                     }), 200

        elif intent == "view_clearance_sales":

            # Fetch promotional products from categories API
            try:
                category_resp = requests.get(CATEGORIES_API_URL, timeout=5)
                categories = category_resp.json() if category_resp.status_code == 200 else []
            except Exception as e:
                print("Error fetching categories:", e)
                categories = []

            promo_products = []

            # Extract products with promotion
            for cat in categories:
                for p in cat.get("products", []):
                    if p.get("is_promo"):
                        promo_products.append({
                            "name": p.get("title"),
                            "price": p.get("promo_price") if p.get("is_promo") else p.get("price"),
                            "image": p.get("image"),
                            "description": p.get("description"),
                            "old_price": p.get("price"),
                            "promo_price": p.get("promo_price"),
                            "unit": p.get("unit"),
                            "is_promo": True,
                            "payload": f"ORDER_{p.get('title').upper().replace(' ', '_')}"
                        })

            message_text = "🔥 Here are your clearance sale items!" if promo_products else "No clearance sale items available right now."

            return jsonify({
                "success": True,
                "step": "view_clearance_sales",
                "llama_answer": message_text,
                "products": promo_products,
                "categories": [],
                "intent": "view_clearance_sales"
            }), 200



        elif intent == "cancel_order":
            # orders = get_pending_orders(sender_id)

            # for order in orders:
            #     if order:
            #         update_order_status(order["id"], "cancelled")
            #     # send_instagram_message(sender_id, f"❌ Your order for {order['product_name']} has been cancelled.")
            message_text = f"❌ Your order has been cancelled."
            return jsonify({
                                "success": True,
                                "step": "cancelling_order",
                                "llama_answer": message_text,
                                "products": [],
                                "categories": [],
                                "intent" : "cancel_order"
                            }), 200


        else:
            print(intent)

            print("💬 LLM failed → Applying fuzzy fallback matching...")
            text_lower = question.lower()

            # Fetch categories if not already fetched
            all_categories = fetch_all_categories()  # <-- your existing function

            matched_categories, matched_products = fallback_category_match(text_lower, all_categories)

            # If nothing matched → truly no idea, return default
            if not matched_categories and not matched_products:
                return {"status": "ok", "llama_answer" : "I couldn't recognize the category. Please try with a different name 🙏"}

            # If we got matched categories → directly show products of the best matched category
            if matched_categories:
                best_category = matched_categories[0]   # pick the top matched category
                category_products = best_category.get("products", [])


                print("I sent that category")
                if category_products:

                    return jsonify({
                        "success": True,
                        "categories" : best_category,
                        "products": category_products,
                        "intent" : "show_products",
                        "llama_answer": f"Here are all our products of {category_filter}"

                    }), 200
                else:

                    return jsonify({
                        "success": True,
                        "categories" : [],
                        "products": [],
                        "intent" : "show_products",
                        "llama_answer": f"This category has no products right now 🙏"

                    }), 200


            # If only products matched → send quick replies
            if matched_products:
                # Take top N products (say 5)
                top_products = matched_products[:5]

                # Add payload to each product
                for p in matched_products:
                    title = p.get("title", "")
                    p["payload"] = f"ORDER_{title.upper().replace(' ', '_')}"

                print("top products", top_products)

                if len(matched_products) == 1:
                    prompt_text = f"🤔 We found a product that seems to match your need:\nPlease choose if this is the one you're looking for."
                else:
                    prompt_text = f"🤔 We found multiple products similar to your search.\nPlease choose the one you are looking for from the list below:"

                return jsonify({
                        "success": True,
                        "categories" : [],
                        "products": matched_products,
                        "intent" : "search_products",
                        "llama_answer": f"{prompt_text}"

                    }), 200  
                # return {"status": "ok"}

        # Final fallback: LLaMA answer
        if not llama_answer:
            llama_answer = "Here are all our categories."

        # Save conversation
        msg = {"question": question, "answer": llama_answer, "timestamp": datetime.datetime.now().isoformat()}
        # r.rpush(session_key, json.dumps(msg))
        # r.ltrim(session_key, -MAX_PREV_CTX, -1)

        return jsonify({
            "llama_answer": llama_answer,
            "categories": [] ,
            "products": [] ,
            "ready_to_call_api": False
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
