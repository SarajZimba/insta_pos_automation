from flask import Flask, request, jsonify, Blueprint
import requests
import os
from dotenv import load_dotenv
import mysql.connector
from insta_routes.convert_to_words import convert_amount_to_words
from utils.ollama_helper import query_ollama

load_dotenv()

instagram_receive = Blueprint("instagram_receive", __name__)

VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN')
GRAPH_API_URL = os.getenv('GRAPH_API_URL')
PRODUCT_API_URL = os.getenv('PRODUCT_API_URL')
ECOM_ACCESS_TOKEN = os.getenv('ECOM_ACCESS_TOKEN')

# MySQL database connection
def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('host'),
        user=os.getenv('user'),
        password=os.getenv('password'),
        database=os.getenv('database')
    )

# Fetch products from the database
def fetch_products():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT product_name, price, image_url FROM products")
    products = cursor.fetchall()
    cursor.close()
    connection.close()
    return {product['product_name'].lower(): product for product in products}

def fetch_products_from_api():
    try:
        product_api_url = "http://103.250.132.116:8077/api/product-list/"
        resp = requests.get(product_api_url, timeout=5)
        if resp.status_code == 200:
            products = resp.json()
            # Convert list into a dict like your old structure {product_name: {...}}
            return {item['title'].lower(): item for item in products}
        else:
            return {}
    except Exception as e:
        print("Error fetching products from API:", e)
        return {}
    
import re
import requests
import json
NEGATIVE_PATTERNS = [
    r"\bdo not show\b",
    r"\bdon't show\b",
    r"\bhide\b",
    r"\bnot interested\b",
    r"\bskip\b",
    r"\bno products\b",
    r"\bno items\b"
]

PRODUCT_PATTERNS = [
    r"\bproduct\b",
    r"\bproducts\b",
    r"\bitem\b",
    r"\bitems\b",
    r"\bmenu\b",
    r"\bmenus\b",
    r"\bdish\b",
    r"\bdishes\b",
    r"\bfood\b"
    r"\bfoods\b"
]

CATEGORY_PATTERNS = [
    r"\bcategory\b",
    r"\bcategories\b",
    r"\bgroup\b",
    r"\bsection\b",
    r"\btype\b"
]

def contains_pattern(text, patterns):
    """Check if any regex pattern matches in the text."""
    text = text.lower()
    return any(re.search(pat, text) for pat in patterns)


import redis
import datetime
import json

# 🔹 Initialize Redis
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
REDIS_CONTEXT_LIMIT = 5   # keep last 5 messages per user

def get_conversation_context(sender_id):
    """Fetch last few messages from Redis and build context."""
    session_key = f"session:instagram:{sender_id}"
    previous_msgs = r.lrange(session_key, -REDIS_CONTEXT_LIMIT, -1)
    if not previous_msgs:
        return ""

    history_context = ""
    for msg in previous_msgs:
        data = json.loads(msg)
        q = data.get("question", "")
        a = data.get("answer", "")
        history_context += f"User: {q}\nAssistant: {a}\n"
    return history_context

def save_message_to_redis(sender_id, question, answer):
    """Store Q/A pair in Redis for future context."""
    session_key = f"session:instagram:{sender_id}"
    message = {
        "question": question,
        "answer": answer,
        "timestamp": datetime.datetime.now().isoformat()
    }
    r.rpush(session_key, json.dumps(message))
    r.ltrim(session_key, -REDIS_CONTEXT_LIMIT, -1)


PRODUCTS = {}

import re


@instagram_receive.route('/instagram_receive', methods=['POST', 'GET'])
def handle_instagram_messages():

    if request.method == 'GET':
        # Instagram Webhook Verification
        hub_mode = request.args.get("hub.mode")
        hub_challenge = request.args.get("hub.challenge")
        hub_verify_token = request.args.get("hub.verify_token")

        print(hub_mode)
        print(hub_challenge)
        print(hub_verify_token)
        if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
            print("Webhook verified successfully!")
            return hub_challenge, 200  # Respond with the challenge token
        else:
            return "Verification failed", 403
    """Handles incoming Instagram messages"""
    data = request.json
    print(data)
    if "entry" in data:
        for entry in data["entry"]:
            for message in entry.get("messaging", []):
                sender_id = message["sender"]["id"]

                if message.get("message", {}).get("is_echo"):
                    print("🔁 Skipping echo message...")
                    continue

                # Handle postback from carousel buttons
                if "postback" in message:
                    print("postback received")
                    payload = message["postback"]["payload"]
                    handle_postback(sender_id, payload)
                    continue

                elif "quick_reply" in message.get("message", {}):
                    payload = message["message"]["quick_reply"]["payload"]

                    if payload.startswith("PRODUCT_"):
                        product_name = payload.replace("PRODUCT_", "").replace("_", " ")
                        print(f"User selected product: {product_name}")

                        # 3️⃣ Send product details
                        product = get_product_by_name(product_name)  # Fetch from your DB/API
                        send_instagram_product_details(sender_id, product)

                    elif payload.startswith("ORDER_"):
                        # User clicked "Order" button for a product
                        product_name = payload.replace("ORDER_", "").replace("_", " ")
                        print(f"User wants to order: {product_name}")

                        # Save order in DB / start order flow
                        # save_order_to_db(sender_id, product_name)
                        send_instagram_message(sender_id, f"You selected {product_name}. Please enter the quantity:")

                    # ---------- CATEGORY SELECTION ----------
                    elif payload.startswith("CATEGORY_"):
                        category_name = payload.replace("CATEGORY_", "").replace("_", " ")
                        print(f"User selected category: {category_name}")

                        # Fetch products for this category
                        resp = requests.get("https://vibezdc.silverlinepos.com/api/categories/", timeout=5)
                        all_categories = resp.json() if resp.status_code == 200 else []

                        selected_category = next((c for c in all_categories if c['title'].lower() == category_name.lower()), None)

                        if selected_category:
                            products_to_send = selected_category.get("products", [])
                            if products_to_send:
                                # Send as carousel
                                send_instagram_carousel_initial(sender_id, products_to_send)

                                # Optionally store products globally for session
                                global PRODUCTS
                                PRODUCTS = {p['title'].lower(): p for p in products_to_send}
                            else:
                                send_instagram_message(sender_id, f"No products found in category '{category_name}'.")
                        else:
                            send_instagram_message(sender_id, f"Category '{category_name}' not found.")

                    continue 
                text = message.get("message", {}).get("text", "").lower()
                print(text)
                message_obj = message.get("message", {})
                if not text or message_obj.get("is_echo"):
                    continue

                text_lower = text.lower()
                negative_intent = contains_pattern(text_lower, NEGATIVE_PATTERNS)

                # --- Free-text messages ---
                text = message.get("message", {}).get("text", "").strip()
                if not text:
                    continue

                text_lower = text.lower()
                negative_intent = contains_pattern(text_lower, NEGATIVE_PATTERNS)

                if negative_intent:
                    continue

                greetings = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening"]

                context = ""
                if text_lower in greetings:
                    llama_intent = {"intent": "small_talk", "category_filter": None, "product_name": None, "negative_intent": False}
                else:
                    llama_intent_raw = query_ollama(text, context)
                    llama_intent = json.loads(llama_intent_raw)

        

                intent = llama_intent.get("intent", "none")
                category_filter = llama_intent.get("category_filter")
                negative_intent = llama_intent.get("negative_intent", False)
                product_name = llama_intent.get("product_name")
                print("intent", intent)
                print("category_filter",category_filter)
                print("negative_intent", negative_intent)
                print("type of category_filter", type(category_filter))
                print("type of product_name", product_name)

                # --- Fetch all categories ---
                resp = requests.get("https://vibezdc.silverlinepos.com/api/categories/", timeout=5)
                all_categories = resp.json() if resp.status_code == 200 else []

                # Check pending order first
                order = get_pending_order(sender_id)
                if order and text.isdigit():
                    quantity = int(text)
                    product_key = order['product_name'].lower()
                    total_price = float(PRODUCTS[product_key]['price']) * quantity
                    update_order_quantity(order['id'], quantity, total_price)
                    send_instagram_message(sender_id, f"✅ Order updated: {quantity} x {order['product_name']} for Rs {total_price}. Type 'confirm' to confirm.")
                    continue  # <--- important, skip all other intent checks

                elif intent == "small_talk":
                    send_instagram_message(sender_id, "Hello! Would you like to view our products for order?")
                    continue
                # --- Handle category or product intent ---
                elif intent == "show_categories":
                    quick_replies = []
                    for cat in all_categories[:13]:
                        quick_replies.append({
                            "content_type": "text",
                            "title": cat['title'][:20],
                            "payload": f"CATEGORY_{cat['title'].upper().replace(' ', '_')}"
                        })
                    payload = {
                        "recipient": {"id": sender_id},
                        "message": {"text": "Please select a category 👇", "quick_replies": quick_replies}
                    }
                    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
                    requests.post(GRAPH_API_URL, headers=headers, json=payload)
                    continue

                elif intent == "show_products" and not category_filter:
                    # show all categories as quick replies
                    quick_replies = [{
                        "content_type": "text",
                        "title": cat['title'][:20],
                        "payload": f"CATEGORY_{cat['title'].upper().replace(' ', '_')}"
                    } for cat in all_categories[:13]]

                    payload = {
                        "recipient": {"id": sender_id},
                        "message": {"text": "Please select a category to see its products 👇", "quick_replies": quick_replies}
                    }
                    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
                    requests.post(GRAPH_API_URL, headers=headers, json=payload)
                    continue

                elif intent == "show_products" and category_filter:
                    selected_category = next((c for c in all_categories if category_filter.lower() in c['title'].lower()), None)
                    if selected_category:
                        products_to_send = selected_category.get("products", [])
                        if products_to_send:
                            send_instagram_carousel_initial(sender_id, products_to_send)
                            PRODUCTS = {p['title'].lower(): p for p in products_to_send}
                            continue  # move to next message
                        else:
                            send_instagram_message(sender_id, f"No products found in category '{selected_category['title']}'.")
                            continue
                    else:
                        send_instagram_message(sender_id, f"Category '{category_filter}' not found.")
                        continue

                elif intent == "place_order":
                    order_items = llama_intent.get("order_items", [])

                    # ✅ If LLM detected multiple items
                    if order_items and len(order_items) > 0:
                        order_summary = []  # to prepare a reply like "Added 3 burgers, 2 pizzas..."

                        for item in order_items:
                            product_name = item.get("product")
                            quantity = item.get("quantity")

                            if not product_name or not quantity:
                                continue  # skip incomplete entries

                            product_key = product_name.lower()

                            if product_key in PRODUCTS:
                                total_price = float(PRODUCTS[product_key]["price"]) * int(quantity)
                                save_order_to_db_all(sender_id, product_name, quantity, total_price)

                                order_summary.append(f"{quantity} x {product_name} (Rs {total_price})")

                        if order_summary:
                            # summary_text = "✅ Order placed: " + ", ".join(order_summary) + ". Type 'confirm' to confirm the order."
                            summary_text = (
                                "✅ Order placed: " + ", ".join(order_summary) + 
                                ".\n\nTo confirm delivery, reply exactly in this format:\n"
                                "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>\n\n"
                                "Example:\nCONFIRM | Name: John Doe | Address: Baneshwor, KTM | Phone: 9812345678"
                            )
                            send_instagram_message(sender_id, summary_text)
                            continue

                    # 🚨 Fallback for older code (single item detection or regex)
                    product_name = llama_intent.get("product_name")
                    if product_name:
                        product_key = product_name.lower()

                        # Try regex quantity if LLM failed
                        quantity_match = re.search(r'\b(\d+)\b', text)
                        if quantity_match:
                            quantity = int(quantity_match.group(1))
                            total_price = float(PRODUCTS[product_key]["price"]) * quantity
                            save_order_to_db_all(sender_id, product_name, quantity, total_price)
                            send_instagram_message(sender_id, f"✅ Order placed: {quantity} x {product_name} for Rs {total_price}. Type 'confirm' to confirm the order.")
                            continue

                        # Ask user for quantity if still missing
                        save_order_to_db(sender_id, product_name)
                        send_instagram_message(sender_id, f"You selected {product_name}. Please enter the quantity:")
                        continue

                # elif intent == "confirm_order":
                #     orders = get_pending_orders(sender_id)
                #     if orders:
                #         total_amount = 0
                #         summary_lines = []

                #         for order in orders:
                #             update_order_status(order["id"], "confirmed")
                #             total_amount += float(order["total_price"])
                #             summary_lines.append(f"{order['quantity']} x {order['product_name']} (Rs {order['total_price']})")

                #         summary_text = "✅ Order confirmed:\n" + "\n".join(summary_lines) + f"\nTotal: Rs {total_amount}"
                #         send_instagram_message(sender_id, summary_text)
                #         continue

                elif intent == "confirm_order":
                    orders = get_pending_orders(sender_id)
                    if not orders:
                        send_instagram_message(sender_id, "You have no pending orders to confirm.")
                        continue

                    customer_details = llama_intent.get("customer_details", {})
                    name = customer_details.get("name")
                    address = customer_details.get("address")
                    phone = customer_details.get("phone")

                    print("name", name)
                    print("address", address)
                    print("phone", phone)

                    if not name or not address or not phone:
                        send_instagram_message(
                            sender_id,
                            "Please provide your Name, Address, and Phone in this format:\n"
                            "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                        )
                        continue

                    # Call delivery API
                    response, total_amount = create_delivery(orders, customer_details)

                    if response.status_code in [200, 201]:
                        # Update order status in DB
                        for order in orders:
                            update_order_status(order["id"], "confirmed")

                        send_instagram_message(
                            sender_id,
                            f"✅ Your order has been confirmed and delivery created!\n"
                            f"Total: Rs {total_amount}\n"
                            f"Name: {name}, Address: {address}, Phone: {phone}"
                        )
                    else:
                        send_instagram_message(sender_id, "❌ Failed to create delivery. Please try again.")
                    continue

                elif intent == "cancel_order":
                    order = get_pending_orders(sender_id)
                    if order:
                        update_order_status(order["id"], "cancelled")
                        send_instagram_message(sender_id, f"❌ Your order for {order['product_name']} has been cancelled.")
                        continue

                elif intent == "product_question" and product_name:
                    send_instagram_product_details(sender_id, "Working on this feature")
                    continue

                else:
                    try:

                        context = (
                            "You are Silverline's AI intelligent assistant. "
                            "Silverline is a skilled backend developer and AI enthusiast with strong expertise in Python, Django, and Flask. "
                            "They frequently work with MySQL databases, REST APIs, and deployment setups using Nginx and Eventlet. "
                            "They also integrate advanced AI/ML features such as YOLO-based computer vision, sales forecasting, and QR code automation. "
                            "Silverline’s projects often involve practical business systems — including canteen billing, loyalty rewards, and inventory management. "
                            "They prefer efficient, accurate, and production-ready code solutions over long theoretical explanations. "
                            "Silverline typically works from 10 AM to 7 PM Nepal time, sometimes extending into the night for debugging or deep technical work. "
                            "They are detail-oriented, performance-focused, and like step-by-step improvements rather than full rewrites. "
                            "Your goal as their AI assistant is to act as an intelligent technical co-developer — helping design, debug, optimize, "
                            "and explain backend logic, database queries, and AI integrations clearly and professionally."
                            "You help users with order status and general inquiries."
                            "You can also show products."

                        )

                        # The user's message becomes the 'question'
                        response = query_ollama(context, text)

                    except Exception as e:
                        print("⚠️ Ollama error:", e)
                        response = "Sorry, I couldn’t process your message right now."

                    send_instagram_message(sender_id, response)

    return jsonify({"status": "received"}), 200


def send_instagram_message(recipient_id, text):
    """Send a text message to Instagram"""
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    print(ACCESS_TOKEN)
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    requests.post(GRAPH_API_URL, headers=headers, json=payload)

def send_instagram_image(recipient_id, image_url):
    """Send an image to Instagram"""
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"attachment": {
            "type": "image",
            "payload": {"url": image_url, "is_reusable": True}
        }}
    }
    
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    requests.post(GRAPH_API_URL, headers=headers, json=payload)

def save_order_to_db(sender_id, product_name):
    """Save order details to the database with 'pending' status"""
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO orders (sender_id, product_name, quantity, total_price, status)
        VALUES (%s, %s, %s, %s, %s)
    """, (sender_id, product_name, 0, 0, 'pending'))
    connection.commit()
    cursor.close()
    connection.close()

def save_order_to_db_all(sender_id, product_name, quantity, total_price):
    """Save order details to the database with 'pending' status"""
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO orders (sender_id, product_name, quantity, total_price, status)
        VALUES (%s, %s, %s, %s, %s)
    """, (sender_id, product_name, quantity, total_price, 'pending'))
    connection.commit()
    cursor.close()
    connection.close()

def get_pending_order(sender_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True, buffered=True)
    cursor.execute("""
        SELECT * FROM orders WHERE sender_id = %s AND status = 'pending'
    """, (sender_id,))
    order = cursor.fetchone()
    cursor.close()
    connection.close()
    return order

def get_pending_orders(sender_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM orders
        WHERE sender_id = %s AND status = 'pending'
    """, (sender_id,))
    orders = cursor.fetchall()
    cursor.close()
    connection.close()
    return orders


def update_order_quantity(order_id, quantity, total_price):
    """Update the order with quantity and total price"""
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("""
        UPDATE orders SET quantity = %s, total_price = %s WHERE id = %s
    """, (quantity, total_price, order_id))
    connection.commit()
    cursor.close()
    connection.close()

def update_order_status(order_id, status):
    """Update the order status to 'confirmed'"""
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("""
        UPDATE orders SET status = %s WHERE id = %s
    """, (status, order_id))
    connection.commit()
    cursor.close()
    connection.close()

def post_order_to_ecom(order, sender_id):
    customer_name = get_username(sender_id)
    url = "https://ecom.silverlinepos.com/api/orders/"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ECOM_ACCESS_TOKEN}"
    }

    data = {
        "customer": None,
        "customer_name": customer_name,
        "customer_address": "instagram",
        "customer_tax_number": "",
        "sub_total": float(order["total_price"]),
        "discount_amount": 0.00,
        "taxable_amount": float(order["total_price"]),
        "tax_amount": round(float(order["total_price"]) * 0.13, 2),
        "grand_total": round(round(float(order["total_price"]), 2) + round(float(order["total_price"]) * 0.13, 2),2),
        "service_charge": 0.00,
        "amount_in_words": convert_amount_to_words(round(float(order["total_price"]) + float(order["total_price"]) * 0.13, 2)),
        "payment_mode": "Cash",
        "order_items": []
    }

    order_item = {
        "product": 1307,
        "product_quantity": float(order["quantity"]),
        "rate": round(float(order["total_price"]) / float(order["quantity"]), 2),
        "unit_title": "pcs",
        "amount": round(float(order["total_price"]), 2),
        "is_taxable": True
    }

    data["order_items"].append(order_item)

    response = requests.post(url, headers=headers, json=data)

    if response.status_code == 201:
        print("✅ Order posted successfully!")
        print(response.json())  # Print response for debugging
        return response.json()
    else:
        print("❌ Failed to post order:", response.status_code, response.text)
        return None

def get_username(SENDER_ID):
    url = f"https://graph.instagram.com/v18.0/{SENDER_ID}?fields=username&access_token={ACCESS_TOKEN}"
    response = requests.get(url)
    user_data = response.json()

    if "username" in user_data:
        print(f"Username: {user_data['username']}")
        return user_data["username"]
    else:
        print("Failed to retrieve username:", user_data)
        return "Unknown"



def send_instagram_carousel_initial(recipient_id, products):
    """
    products: list of dicts, each dict with 'title', 'image', 'price', 'url' (optional)
    """
    elements = []
    for p in products[:10]:  # Instagram allows up to 10 elements per carousel
        element = {
            "title": p["title"],
            "image_url": p.get("image"),
            "subtitle": f"Price: Rs {p['price']}",
            # "buttons": [
            #     {
            #         "type": "postback",
            #         # "type": "reply",
            #         "title": f"Order {p['title']}",
            #         "payload": f"ORDER_{p['title']}"
            #     }
            # ]
        }
        elements.append(element)

    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "generic",
                    "elements": elements
                }
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(GRAPH_API_URL, headers=headers, json=payload)
    print(response.status_code, response.text)

def send_instagram_carousel(recipient_id, products):
    """
    Send quick reply buttons instead of carousel (Instagram safe version)
    """
    quick_replies = []
    for p in products[:10]:
        quick_replies.append({
            "content_type": "text",
            "title": p["title"][:20],  # IG limit
            # "title": f"PRODUCT_{p['title'].upper().replace(' ', '_')}",

            "payload": f"PRODUCT_{p['title'].upper().replace(' ', '_')}"
        })

    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": "Please select a product 👇 or  Type 'order ProductName' to order any product.",
            "quick_replies": quick_replies
        }
    }

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    # send_instagram_carousel_initial(recipient_id, products)
    response = requests.post(GRAPH_API_URL, headers=headers, json=payload)
    print("IG PRODUCT MENU:", response.status_code, response.text)



def handle_postback(sender_id, payload):
    """
    Handle postback from carousel buttons.
    Payload format: ORDER_ProductName
    """
    if payload.startswith("ORDER_"):
        product_name = payload.replace("ORDER_", "").strip().lower()
        global PRODUCTS

        if product_name in PRODUCTS:
            save_order_to_db(sender_id, product_name)
            send_instagram_message(
                sender_id,
                f"You selected {product_name}. Price: Rs {PRODUCTS[product_name]['price']}. Please enter the quantity."
            )
        else:
            send_instagram_message(sender_id, "❌ Product not found. Please type the product name manually.")


def send_instagram_product_details(recipient_id, product):
    # Send image
    image_payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": product["image"]}
            }
        }
    }
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    requests.post(GRAPH_API_URL, headers=headers, json=image_payload)

    # Send order prompt
    order_payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": f"{product['title']} - Rs {product['price']}\nDo you want to order this?",
            "quick_replies": [
                {"content_type": "text", "title": f"Order {product['title']}", "payload": f"ORDER_{product['title'].upper().replace(' ', '_')}"},
                {"content_type": "text", "title": "❌ Cancel", "payload": "CANCEL_ORDER"}
            ]
        }
    }
    requests.post(GRAPH_API_URL, headers=headers, json=order_payload)


def get_product_by_name(product_name: str):
    """
    Fetch product info from the API based on name.
    """
    try:
        resp = requests.get("https://vibezdc.silverlinepos.com/api/categories/", timeout=5)
        categories = resp.json() if resp.status_code == 200 else []

        for cat in categories:
            for prod in cat.get("products", []):
                if prod["title"].lower() == product_name.lower():
                    return prod
    except Exception as e:
        print("⚠️ Error fetching product:", e)

    return None

import requests
import json
from datetime import datetime

# -------------------- DELIVERY API FUNCTION --------------------
def create_delivery(orders, customer_details):
    """
    Post delivery to the delivery API.
    orders: list of pending order dicts
    customer_details: dict with keys name, address, phone
    """
    delivery_items = []
    total_amount = 0

    for order in orders:
        product_name = order["product_name"].lower()
        quantity = float(order["quantity"])
        total_price = float(order["total_price"])
        total_amount += total_price

        # Match product ID from PRODUCTS dict (fetched previously from API)
        product_info = next((p for p in PRODUCTS.values() if p["title"].lower() == product_name), None)
        if not product_info:
            continue

        delivery_items.append({
            "product": product_info["id"],
            "quantity": quantity
        })

    # Generate bill_no using first order id or timestamp
    bill_no = f"BIL-{orders[0]['id']}-{int(datetime.now().timestamp())}"

    # Prepare payload
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "deliveryDate": (datetime.now()).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deliver_to": customer_details.get("address"),
        "special_request": "",
        "Current_state": "Ordered",
        "delivery_option": "Express",
        "bill_no": bill_no,
        "customer": {
            "name": customer_details.get("name"),
            "tax_number": "",
            "address": customer_details.get("address"),
            "contact_number": customer_details.get("phone"),
            "email": "",
            "branch": None
        },
        "delivery_details": delivery_items
    }

    # Post to API
    api_url = "https://vibezdc.silverlinepos.com/api/delivery-create/"
    headers = {"Content-Type": "application/json"}
    response = requests.post(api_url, headers=headers, json=payload)
    return response, total_amount

