from flask import Flask, request, jsonify, Blueprint
import requests
import os
from dotenv import load_dotenv
import mysql.connector
from insta_routes.convert_to_words import convert_amount_to_words
from utils.ollama_helper import query_ollama,query_ollama_confirmation,query_ollama_quantity, query_ollama_color, query_ollama_size, query_ollama_name, query_ollama_phone, query_ollama_address

from helper_func import is_duplicate_message, save_processed_message

load_dotenv()


from sentence_transformers import SentenceTransformer, util
import torch

# Initialize the embedding model (you can choose any model you like)
embedder = SentenceTransformer('all-MiniLM-L6-v2')  # small and fast


instagram_receive = Blueprint("instagram_receive", __name__)

VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN')
GRAPH_API_URL = os.getenv('GRAPH_API_URL')
PRODUCT_API_URL = os.getenv('PRODUCT_API_URL')
ECOM_ACCESS_TOKEN = os.getenv('ECOM_ACCESS_TOKEN')
API_URL = os.getenv('API_URL')

CATEGORIES_API_URL = API_URL + "api/categories/"

PRODUCT_ATTRIBUTES_API_URL = API_URL + "api/products/attributes/name"
NEGATIVE_SALES_API_URL = API_URL + "api/allow-negative-sales/"

STOCKBYNAME_API_URL = API_URL + "api/products/stockbyname/"

DELIVERY_API_URL = API_URL + "api/delivery-create/"

PRODUCT_SIZES_BY_COLOR_API_URL = API_URL + "api/productsizebycolor/"

PRODUCT_VARIANT_CHECK_API_URL = API_URL + "api/productvariantcheck/" 

PRODUCT_ATTRIBUTES_BY_NAME_API_URL = API_URL + "api/products/attributes/name"

PRODUCT_STOCK_BY_NAME_API_URL = API_URL + "api/products/stockbyname/"

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
import threading


AFFIRMATIVE = {"yes", "yeah", "sure", "ok", "okay", "ha", "hajur", "yo", "ho", "hun"}
NEGATIVE = {"no", "nah", "nahi", "cancel"}

@instagram_receive.route('/instagram_receive', methods=['POST', 'GET'])
# @instagram_receive.route('/instagram_receive_slot_test', methods=['POST', 'GET'])
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

    # ✅ Respond IMMEDIATELY (prevents Instagram retry spam)
    threading.Thread(target=process_message_async, args=(data,)).start()
    return "EVENT_RECEIVED", 200



def process_message_async(data):
    print(data)
    if "entry" in data:
        for entry in data["entry"]:
            for message in entry.get("messaging", []):
                sender_id = message["sender"]["id"]

                recipient_id = message["recipient"]["id"]

                # ✅ Skip messages sent by your own Instagram Page
                if sender_id == recipient_id:
                    print("🔁 Skipping self message (bot output)")
                    continue

                if message.get("message", {}).get("is_echo"):
                    print("🔁 Skipping echo message...")
                    continue

                if message.get("message", {}).get("mid"):
                    message_id = message.get("message", {}).get("mid")

                # if "attachments" in message.get("message", {}):
                #     for attachment in message["message"]["attachments"]:
                #         if attachment["type"] == "image":
                #             image_url = attachment["payload"]["url"]
                #             from .image import handle_instagram_image
                #             handle_instagram_image(sender_id, image_url)
                #             # continue
                #             return
                message_obj = message.get("message", {})
                attachments = message_obj.get("attachments", [])

                if attachments:
                    # 1️⃣ Check if user is in confirm_order awaiting screenshot
                    confirm_state_json = r.get(f"confirm_order:{sender_id}")
                    if confirm_state_json:
                        confirm_state = json.loads(confirm_state_json)
                        if confirm_state["step"] == "awaiting_payment_screenshot":
                            # handle_payment_screenshot(sender_id, attachments, confirm_state)
                            # continue


                        # elif step == "awaiting_payment_screenshot":
                            # message_obj = message.get("message", {})
                            print("inside awaiting attachment")
                            # attachments = message_obj.get("attachments", [])

                            if attachments:
                                image_found = False

                                for att in attachments:
                                    if att.get("type") == "image":
                                        image_found = True

                                        # ✅ Extract URL
                                        image_url = att["payload"]["url"]
                                        confirm_state["payment"]["screenshot"] = image_url
                                        r.set(f"confirm_order:{sender_id}", json.dumps(confirm_state))

                                        # ✅ Reload data
                                        details = confirm_state["customer_details"]
                                        orders = confirm_state["orders"]

                                        # ✅ Confirm order
                                        response, total_amount = create_delivery(orders, details)
                                        if response.status_code in [200, 201]:
                                            for order in orders:
                                                update_order_status(order["id"], "confirmed")
                                                update_order_payment_mode(order["id"], confirm_state["payment"]["mode"])

                                            # ✅ Clear confirm_state
                                            r.delete(f"confirm_order:{sender_id}")
                                            send_instagram_message(
                                                sender_id,
                                                f"🎉 Order Confirmed!\n"
                                                f"Total: Rs {total_amount}\n"
                                                f"{details['name']} | {details['phone']}\n"
                                                f"{details['address']}\n\n"
                                                "Payment screenshot received. Thank you! 🙏"
                                            )
                                            return
                                        else:
                                            send_instagram_message(sender_id, "❌ Failed to create delivery. Please try again.")
                                            return

                                        # return  # stop processing further

                                if not image_found:
                                    # Attachments exist but no image
                                    send_instagram_message(sender_id, "⚠️ Please upload a **valid payment screenshot** (image).")
                                    return

                            # No attachments at all
                            send_instagram_message(sender_id, "📸 Please upload your payment screenshot to confirm your order.")
                            continue

                    # 2️⃣ Otherwise, treat as product image for search
                    for attachment in attachments:
                        if attachment["type"] == "image":
                            image_url = attachment["payload"]["url"]
                            from .image import handle_instagram_image
                            handle_instagram_image(sender_id, image_url)
                            return
                        
                        else:
                            print("⚠️ Skipping non-image attachments:")
                            # don’t treat it as address
                            return

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
                                send_instagram_message(sender_id, f"❌ Could not find the product '{product_name}'.")
                                continue

                            # 3️⃣ Default quantity = 1
                            quantity = 0
                            print("matched product from category api", matched_product)
                            is_promo = matched_product["is_promo"]
                            price = float(matched_product["price"])

                            if is_promo:
                                price = float(matched_product.get("promo_price", 0.0))
                            total_price = price * quantity

                            # 4️⃣ Save order to DB
                            # save_order_to_db_all(sender_id, matched_product["title"], quantity, price, total_price)

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

                            # # 6️⃣ Ask for attributes if available, else confirm order
                            # if color_options or size_options:
                            #     attr_message = f"⚙️ Please specify the missing details for your product {matched_product['title']}:\n\n"
                            #     if size_options:
                            #         attr_message += "📏 *Available Sizes:*\n" + "\n".join(f"   - {s}" for s in size_options)
                            #     if color_options:
                            #         attr_message += "\n\n🎨 *Available Colors:*\n" + "\n".join(f"   - {c}" for c in color_options)
                            #     attr_message += (
                            #         "\n\n📝 *Reply in this format:*\n"
                            #         "👉 `XL Golden, L Blue, XXL Red`\n\n"
                            #         "Each item should include size and color if applicable."
                            #     )
                            #     send_instagram_message(sender_id, attr_message)

                            # else:
                            #     connection = get_db_connection()
                            #     cursor = connection.cursor(dictionary=True)
                            #     # ✅ No attributes → now check for quantity requirement
                            #     cursor.execute("""
                            #         SELECT id, product_name FROM orders
                            #         WHERE sender_id = %s AND status='pending' AND quantity=0
                            #         ORDER BY id ASC LIMIT 1
                            #     """, (sender_id,))
                            #     pending_qty = cursor.fetchone()

                            #     cursor.close()
                            #     connection.close()

                            #     if pending_qty:
                            #         print("Enter quantity was sent from here")
                            #         # Ask quantity for only the FIRST pending product
                            #         send_instagram_message(
                            #             sender_id,
                            #             f"📝 Please enter quantity for: *{pending_qty['product_name']}*\n"
                            #             "Reply with a number like:\n"
                            #             "👉 1, 2, 3, four, ten"
                            #             # f"📝 Are you sure you want to order {pending_qty['product_name']}?*\n"
                            #         )
                            #     else:
                            #         # If quantity is already known → proceed to confirmation
                            #         send_instagram_message(
                            #             sender_id,
                            #             f"✅ Added {quantity} x {matched_product['title']} (Rs {price} each).\n"
                            #             f"Total: Rs {total_price}\n\n"
                            #             "🎉 All items ready!\n"
                            #             "To confirm delivery, reply \n"
                            #             "Confirm"
                            #         )

                            if color_options:
                                # Save in Redis that we are waiting for color selection
                                r.set(f"user_state:{sender_id}", json.dumps({
                                        "step": "awaiting_color",
                                        "product_name": matched_product["title"],
                                        "price": price  # ✅ store here
                                }))
                                    # send_instagram_message(sender_id, 
                                    #     f"🎨 Please select a color for {matched_product["title"]}:\n" +
                                    #     "\n".join(f"   - {c}" for c in color_options)
                                    # )

                                    # Build a nice formatted message
                                color_list = "\n".join(f"• {c.capitalize()}" for c in color_options)
                                message_text = (
                                    f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                    f"🎨 Available colors:\n{color_list}\n\n"
                                    f"👉 Please reply with your preferred color name to continue."
                                )
                                r.delete(f"user:{sender_id}:pending_action")

                                send_instagram_message(sender_id, message_text)
                                continue

                            elif size_options:
                                r.set(f"user_state:{sender_id}", json.dumps({
                                    "step": "awaiting_size",
                                    "product_name": matched_product["title"],
                                    "price": price
                                }))

                                size_list = "\n".join(f"• {s.capitalize()}" for s in size_options)
                                message_text = (
                                    f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                    f"📏 Available sizes:\n{size_list}\n\n"
                                    f"👉 Please reply with your preferred size to continue."
                                )
                                r.delete(f"user:{sender_id}:pending_action")
                               
                                send_instagram_message(sender_id, message_text)

                            else:
                                r.set(f"user_state:{sender_id}", json.dumps({
                                    "step": "awaiting_quantity",
                                    "product_name": matched_product["title"],
                                    "price": price
                                }))

                                message_text = (
                                    f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                    f"🧮 Please reply with the quantity you'd like to order.\n"
                                    f"👉 Example: `1`, `2`, or `3`"
                                )
                                r.delete(f"user:{sender_id}:pending_action")
                            
                                send_instagram_message(sender_id, message_text)
                                

                        except Exception as e:
                            r.delete(f"user:{sender_id}:pending_action")

                            print("⚠️ Failed to process quick reply order:", e)
                            send_instagram_message(sender_id, "❌ Something went wrong while processing your order. Please try again.")


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
                                send_instagram_carousel_initial(sender_id, products_to_send)

                                # Optionally store products globally for session
                                global PRODUCTS
                                PRODUCTS = {p['title'].lower(): p for p in products_to_send}
                            else:
                                send_instagram_message(sender_id, f"No products found in category '{category_name}'.")
                        else:
                            send_instagram_message(sender_id, f"Category '{category_name}' not found.")

                    continue


                # Handle free-text messages safely
                message_obj = message.get("message", {})
                text = message_obj.get("text", "")

                # # ✅ Skip empty or echo messages
                # if not text or message_obj.get("is_echo"):
                #     print("🔁 Skipping echo or empty message")
                #     continue

                text_lower = text.lower().strip()
                print("Received:", text_lower)
                negative_intent = contains_pattern(text_lower, NEGATIVE_PATTERNS)


                # Check if sender_id has an ongoing confirm_order flow
                state_json = r.get(f"confirm_order:{sender_id}")
                if state_json:
                    state = json.loads(state_json)
                    step = state["step"]

                    # if step == "asking_name":
                    #     state["customer_details"]["name"] = text
                    #     state["step"] = "asking_address"
                    #     r.set(f"confirm_order:{sender_id}", json.dumps(state))
                    #     send_instagram_message(sender_id, "Thanks! Now please provide your delivery address.")
                    #     continue

                    # elif step == "asking_address":
                    #     state["customer_details"]["address"] = text
                    #     state["step"] = "asking_phone"
                    #     r.set(f"confirm_order:{sender_id}", json.dumps(state))
                    #     send_instagram_message(sender_id, "Almost done! Please provide your phone number.")
                    #     continue


                    # elif step == "asking_phone":
                    #     state["customer_details"]["phone"] = text
                    #     state["step"] = "asking_payment"
                    #     r.set(f"confirm_order:{sender_id}", json.dumps(state))

                    #     payment_modes = get_payment_modes()
                    #     msg = "Please select a payment mode:\n"
                    #     for idx, pm in enumerate(payment_modes, start=1):
                    #         msg += f"{idx}. {pm['mode']}\n"
                    #     msg += "Reply with the number of your preferred payment method."
                    #     r.set(f"confirm_order_payment_modes:{sender_id}", json.dumps(payment_modes))  # store for later
                    #     send_instagram_message(sender_id, msg)
                    #     # continue
                    #     return

                    if step == "asking_name":
                        name = query_ollama_name(text)

                        if name.lower() == "unknown":
                            send_instagram_message(sender_id, "⚠️ I couldn't understand your name. Please enter it again.")
                            continue  # stay in the same step

                        state["customer_details"]["name"] = name
                        state["step"] = "asking_phone"
                        r.set(f"confirm_order:{sender_id}", json.dumps(state))
                        send_instagram_message(sender_id, "Great! Please provide your phone number.")
                        continue

                    elif step == "asking_phone":
                        phone = query_ollama_phone(text)

                        if phone.lower() == "unknown":
                            send_instagram_message(sender_id, "⚠️ I couldn't understand your phone number. Please enter it again.")
                            continue  # stay in the same step
                        state["customer_details"]["phone"] = phone
                        state["step"] = "checking_existing_customer"
                        r.set(f"confirm_order:{sender_id}", json.dumps(state))

                        # ✅ Check if customer exists
                        try:
                            resp = requests.get(f"https://vibezdc.silverlinepos.com/api/check-customer/?phone={phone}", timeout=5)
                            data = resp.json()
                            if data.get("exists"):
                                customer = data["customer"]
                                state["existing_customer"] = customer  # store temporarily
                                state["step"] = "confirm_existing_address"
                                r.set(f"confirm_order:{sender_id}", json.dumps(state))

                                send_instagram_message(
                                    sender_id,
                                    f"📍 We found your saved address:\n\n{customer['address']}\n\n"
                                    f"Would you like to use this address?"
                                )
                                continue
                        except:
                            pass

                        # If no existing customer found → ask address normally
                        state["step"] = "asking_address"
                        r.set(f"confirm_order:{sender_id}", json.dumps(state))
                        send_instagram_message(sender_id, "Please provide your delivery address.")
                        continue

                    elif step == "confirm_existing_address":
                        confirm_intent = query_ollama_confirmation(text_lower)
                        # if text_lower in AFFIRMATIVE:
                        if confirm_intent == "confirm_yes":
                        # if text_lower in ["yes", "y", "ok", "sure"]:
                            # Use existing address
                            customer = state.get("existing_customer", {})
                            state["customer_details"]["address"] = customer.get("address")
                            state["step"] = "asking_payment"
                            r.set(f"confirm_order:{sender_id}", json.dumps(state))

                            # Proceed to payment selection
                            payment_modes = get_payment_modes()
                            msg = "Please select a payment mode:\n"
                            for idx, pm in enumerate(payment_modes, start=1):
                                msg += f"{idx}. {pm['mode']}\n"
                            msg += "Reply with the number of your preferred payment method."

                            r.set(f"confirm_order_payment_modes:{sender_id}", json.dumps(payment_modes))
                            send_instagram_message(sender_id, msg)
                            continue

                        else:
                            # Ask for a new address
                            state["step"] = "asking_address"
                            r.set(f"confirm_order:{sender_id}", json.dumps(state))
                            send_instagram_message(sender_id, "No problem! Please enter your address.")
                            continue

                    elif step == "asking_address":
                        address = query_ollama_address(text)

                        if address.lower() == "unknown":
                            send_instagram_message(sender_id, "⚠️ I couldn't understand your address. Please enter it again.")
                            continue  # stay in the same step
                        
                        state["customer_details"]["address"] = address
                        state["step"] = "asking_payment"
                        r.set(f"confirm_order:{sender_id}", json.dumps(state))

                        # Proceed to payment selection
                        payment_modes = get_payment_modes()
                        msg = "Please select a payment mode:\n"
                        for idx, pm in enumerate(payment_modes, start=1):
                            msg += f"{idx}. {pm['mode']}\n"
                        msg += "Reply with the number of your preferred payment method."

                        r.set(f"confirm_order_payment_modes:{sender_id}", json.dumps(payment_modes))
                        send_instagram_message(sender_id, msg)
                        continue


                    elif step == "asking_payment":
                        payment_modes = json.loads(r.get(f"confirm_order_payment_modes:{sender_id}") or "[]")
                        try:
                            choice = int(text.strip()) - 1
                            if choice < 0 or choice >= len(payment_modes):
                                raise ValueError
                        except ValueError:
                            send_instagram_message(sender_id, "Invalid choice. Please reply with the number of your payment method.")
                            continue

                        selected_payment = payment_modes[choice]
                        state["payment"]["mode"] = selected_payment["mode"]
                        state["payment"]["image"] = selected_payment["image"]
                        state["customer_details"]["payment_mode"] = selected_payment["mode"]  # <-- add this

                        # ✅ Fetch pending orders using your helper
                        orders = get_pending_orders(sender_id)
                        if not orders:
                            r.delete(f"confirm_order:{sender_id}")

                            send_instagram_message(sender_id, "🛒 You have no items in your cart to checkout.")
                            continue
                        
                        print(orders)
                        # Save into conversation state for next step
                        state["orders"] = orders


                        state["step"] = "confirming_order"
                        # r.set(f"confirm_order:{sender_id}", json.dumps(state))
                        r.set(f"confirm_order:{sender_id}", json.dumps(state, default=decimal_default))

                        details = state["customer_details"]
                        payment_msg = f"Payment Mode: {selected_payment['mode']}\n"
                        if selected_payment["image"]:  # only send image if not Cash on Delivery
                            send_instagram_message(sender_id, payment_msg)
                            send_instagram_message(sender_id, selected_payment["image"])  # send QR image
                            
                        else:
                            send_instagram_message(sender_id, payment_msg)
                            
                        send_instagram_message(
                            sender_id,
                            f"✅ Order Details:\n"
                            f"Name: {details['name']}\n"
                            f"Address: {details['address']}\n"
                            f"Phone: {details['phone']}\n\n"
                            # f"Reply 'ok' to confirm."
                            f"Do you want to place your order ?"
                        )
                        continue

                    # elif step == "confirming_order":
                    #     if text.lower() == "ok":
                    #         details = state["customer_details"]
                    #         orders = state["orders"]
                    #         response, total_amount = create_delivery(orders, details)
                    #         if response.status_code in [200, 201]:
                    #             for order in orders:
                    #                 update_order_status(order["id"], "confirmed")
                    #                 update_order_payment_mode(order["id"], state["payment"]["mode"])  # save payment mode
                    #             send_instagram_message(
                    #                 sender_id,
                    #                 f"🎉 Your order has been confirmed!\n"
                    #                 f"Total: Rs {total_amount}\n"
                    #                 f"Name: {details['name']}, Address: {details['address']}, Phone: {details['phone']}"
                    #             )
                    #         else:
                    #             send_instagram_message(sender_id, "❌ Failed to create delivery. Please try again.")
                    #     else:
                    #         send_instagram_message(sender_id, "❌ Order cancelled.")

                    #     # Clear Redis state after confirmation or cancellation
                    #     r.delete(f"confirm_order:{sender_id}")
                    #     continue




                    # elif step == "awaiting_payment_screenshot":
                    #     message_obj = message.get("message", {})
                    #     print("inside awaiting attachment")
                    #     attachments = message_obj.get("attachments", [])

                    #     if attachments:
                    #         image_found = False

                    #         for att in attachments:
                    #             if att.get("type") == "image":
                    #                 image_found = True

                    #                 # ✅ Extract URL
                    #                 image_url = att["payload"]["url"]
                    #                 state["payment"]["screenshot"] = image_url
                    #                 r.set(f"confirm_order:{sender_id}", json.dumps(state))

                    #                 # ✅ Reload data
                    #                 details = state["customer_details"]
                    #                 orders = state["orders"]

                    #                 # ✅ Confirm order
                    #                 response, total_amount = create_delivery(orders, details)
                    #                 if response.status_code in [200, 201]:
                    #                     for order in orders:
                    #                         update_order_status(order["id"], "confirmed")
                    #                         update_order_payment_mode(order["id"], state["payment"]["mode"])

                    #                     # ✅ Clear state
                    #                     r.delete(f"confirm_order:{sender_id}")
                    #                     send_instagram_message(
                    #                         sender_id,
                    #                         f"🎉 Order Confirmed!\n"
                    #                         f"Total: Rs {total_amount}\n"
                    #                         f"{details['name']} | {details['phone']}\n"
                    #                         f"{details['address']}\n\n"
                    #                         "Payment screenshot received. Thank you! 🙏"
                    #                     )
                    #                     return
                    #                 else:
                    #                     send_instagram_message(sender_id, "❌ Failed to create delivery. Please try again.")
                    #                     return

                    #                 # return  # stop processing further

                    #         if not image_found:
                    #             # Attachments exist but no image
                    #             send_instagram_message(sender_id, "⚠️ Please upload a **valid payment screenshot** (image).")
                    #             return

                    #     # No attachments at all
                    #     send_instagram_message(sender_id, "📸 Please upload your payment screenshot to confirm your order.")
                    #     continue



                    # elif step == "confirming_order":
                    #     details = state["customer_details"]
                    #     orders = state["orders"]


                    #     confirm_intent = query_ollama_confirmation(text_lower)
                    #     # Check if payment requires screenshot
                    #     if confirm_intent == "confirm_yes":
                    #         if state["payment"]["mode"].lower() != "cash on delivery":
                    #             state["step"] = "awaiting_payment_screenshot"
                    #             r.set(f"confirm_order:{sender_id}", json.dumps(state))
                    #             send_instagram_message(sender_id, "Please upload your payment screenshot for confirmation. Thank you!")
                    #             continue
                    #         else:
                    #             # Cash on delivery → confirm order immediately
                    #             response, total_amount = create_delivery(orders, details)
                    #             if response.status_code in [200, 201]:
                    #                 for order in orders:
                    #                     update_order_status(order["id"], "confirmed")
                    #                     update_order_payment_mode(order["id"], state["payment"]["mode"])
                    #                 send_instagram_message(
                    #                     sender_id,
                    #                     f"🎉 Your order has been confirmed!\n"
                    #                     f"Total: Rs {total_amount}\n"
                    #                     f"Name: {details['name']}, Address: {details['address']}, Phone: {details['phone']}"
                    #                 )
                    #             else:
                    #                 send_instagram_message(sender_id, "❌ Failed to create delivery. Please try again.")
                    #             r.delete(f"confirm_order:{sender_id}")
                    #             continue

                    #     elif confirm_intent == "confirm_no":
                    #         cancel_pending_orders(sender_id)
                    #         r.delete(f"confirm_order:{sender_id}")
                    #         send_instagram_message(sender_id, "🛑 Your order has been cancelled. You can start a new order anytime.")
                    #         continue
                    #     else:
                    #         send_instagram_message(sender_id, "Sure . But you need to confirm your order to get to next step. Would you like to confirm your order ?")
                    #         continue 

                    elif step == "confirming_order":
                        details = state["customer_details"]
                        orders = state["orders"]

                        # 🧾 Show all items in the order (helpful for user confirmation)
                        order_summary = "🛍️ Here are the items in your order:\n\n"
                        total_price = 0
                        for o in orders:
                            line_total = o["price"] * o["quantity"]
                            total_price += line_total
                            order_summary += (
                                f"• {o['product_name']} "
                                f"({o.get('color', '-')}, {o.get('size', '-')})\n"
                                f"   Qty: {o['quantity']} × Rs {o['price']} = Rs {line_total}\n\n"
                            )

                        order_summary += f"💰 Total: Rs {total_price}\n"
                        send_instagram_message(sender_id, order_summary)

                        confirm_intent = query_ollama_confirmation(text_lower)

                        # ✅ If user confirms
                        if confirm_intent == "confirm_yes":
                            if state["payment"]["mode"].lower() != "cash on delivery":
                                state["step"] = "awaiting_payment_screenshot"
                                r.set(f"confirm_order:{sender_id}", json.dumps(state))
                                send_instagram_message(
                                    sender_id,
                                    "Please upload your payment screenshot for confirmation. Thank you!"
                                )
                                continue
                            else:
                                # ✅ Cash on delivery — confirm immediately
                                response, total_amount = create_delivery(orders, details)
                                if response.status_code in [200, 201]:
                                    for order in orders:
                                        update_order_status(order["id"], "confirmed")
                                        update_order_payment_mode(order["id"], state["payment"]["mode"])
                                    send_instagram_message(
                                        sender_id,
                                        f"🎉 Your order has been confirmed!\n"
                                        f"Total: Rs {total_amount}\n"
                                        f"Name: {details['name']}\n"
                                        f"Address: {details['address']}\n"
                                        f"Phone: {details['phone']}"
                                    )
                                else:
                                    send_instagram_message(sender_id, "❌ Failed to create delivery. Please try again.")
                                r.delete(f"confirm_order:{sender_id}")
                                continue

                        elif confirm_intent == "confirm_no":
                            cancel_pending_orders(sender_id)
                            r.delete(f"confirm_order:{sender_id}")
                            send_instagram_message(
                                sender_id,
                                "🛑 Your order has been cancelled. You can start a new order anytime."
                            )
                            continue

                        else:
                            # If user says something else, re-ask confirmation
                            send_instagram_message(
                                sender_id,
                                "Sure. But please confirm your order to proceed.\nWould you like to confirm your order?"
                            )
                            continue


                attribute_state_json = r.get(f"user_state:{sender_id}")
                if attribute_state_json:
                    state = json.loads(attribute_state_json)
                    step = state.get("step")

                    # if step == "awaiting_color":
                    #     selected_color = text.strip().lower()
                    #     product_name = state["product_name"]

                    #     # ✅ Now fetch available sizes for this color using your API
                    #     size_resp = requests.post(
                    #         PRODUCT_SIZES_BY_COLOR_API_URL,
                    #         json={"title": product_name, "color": selected_color},
                    #         timeout=5
                    #     )
                    #     size_data = size_resp.json()
                    #     sizes = size_data.get("available_sizes", [])

                    #     if sizes:
                    #         # ✅ Save color to Redis temporarily
                    #         r.set(f"user_state:{sender_id}", json.dumps({
                    #             "step": "awaiting_size",
                    #             "product_name": product_name,
                    #             "color": selected_color,
                    #             "price": state["price"],
                    #         }))
                    #         send_instagram_message(sender_id,
                    #             f"📏 Available sizes for color '{selected_color}':\n" +
                    #             "\n".join(f"   - {s}" for s in sizes) +
                    #             "\n\nPlease reply with your desired size."
                    #         )
                    #     else:
                    #         # ✅ Store both color and size, and move to quantity
                    #         r.set(f"user_state:{sender_id}", json.dumps({
                    #             "step": "awaiting_quantity",
                    #             "product_name": product_name,
                    #             "color": selected_color,
                    #             "size": "",
                    #             "price": state["price"],
                    #         }))
                    #         send_instagram_message(sender_id,
                    #             f"❌ No sizes found for '{selected_color}'. Please enter quantity"
                    #         )
                    #     return



                    if step == "awaiting_color":
                        # selected_color = text.strip().lower()
                        selected_color = query_ollama_color(text)
                        # selected_color = text.strip().lower()
                        product_name = state["product_name"]

                        # ✅ Call your exact-match variant check API
                        variant_resp = requests.post(
                            PRODUCT_VARIANT_CHECK_API_URL,  # /api/productvariantcheck/
                            json={"title": product_name, "color": selected_color},
                            timeout=5
                        )
                        variant_data = variant_resp.json()

                        if variant_data.get("exists"):
                            # ✅ Color exists → fetch sizes
                            size_resp = requests.post(
                                PRODUCT_SIZES_BY_COLOR_API_URL,
                                json={"title": product_name, "color": selected_color},
                                timeout=5
                            )
                            size_data = size_resp.json()
                            sizes = size_data.get("available_sizes", [])

                            if sizes:
                                r.set(f"user_state:{sender_id}", json.dumps({
                                    "step": "awaiting_size",
                                    "product_name": product_name,
                                    "color": selected_color,
                                    "price": state["price"],
                                }))
                                send_instagram_message(sender_id,
                                    f"📏 Available sizes for color '{selected_color}':\n" +
                                    "\n".join(f"   - {s}" for s in sizes) +
                                    "\n\nPlease reply with your desired size."
                                )
                                continue
                            else:
                                # No sizes found → skip to quantity
                                r.set(f"user_state:{sender_id}", json.dumps({
                                    "step": "awaiting_quantity",
                                    "product_name": product_name,
                                    "color": selected_color,
                                    "size": "",
                                    "price": state["price"],
                                }))
                                send_instagram_message(sender_id,
                                    f" Great! We got that color selection '{selected_color}'. Now, please enter quantity."
                                )
                                continue

                        else:
                            # ❌ Color not available → stay in same state and show all valid colors
                            send_instagram_message(sender_id,
                                f"❌ The color '{selected_color}' is not available for '{product_name}'."
                            )

                            # ✅ Fetch all valid colors for the product
                            attributes_resp = requests.post(
                                PRODUCT_ATTRIBUTES_BY_NAME_API_URL,  # /api/products/attributes/name
                                json={"product_name": product_name},
                                timeout=5
                            )
                            attributes_data = attributes_resp.json()
                            available_colors = attributes_data.get("attributes", {}).get("color", [])

                            if available_colors:
                                send_instagram_message(sender_id,
                                    "Available colors:\n" +
                                    "\n".join(f"   - {c}" for c in available_colors)
                                )
                                continue
                            else:
                                send_instagram_message(sender_id,
                                    "No colors are currently available for this product."
                                )
                                continue

                    
                    # elif step == "awaiting_size":
                    #     selected_size = text.strip().upper()
                    #     product_name = state["product_name"]
                    #     color = state.get("color", "")

                    #     # ✅ Store both color and size, and move to quantity
                    #     r.set(f"user_state:{sender_id}", json.dumps({
                    #         "step": "awaiting_quantity",
                    #         "product_name": product_name,
                    #         "color": color,
                    #         "size": selected_size,
                    #         "price": state["price"],
                    #     }))

                    #     send_instagram_message(sender_id,
                    #         f"📝 Great! You chose {selected_size} in {color}.\nPlease enter quantity."
                    #     )
                    #     return

                    elif step == "awaiting_size":
                        # selected_size = text.strip().upper()
                        selected_size = query_ollama_size(text)

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
                            r.set(f"user_state:{sender_id}", json.dumps({
                                "step": "awaiting_quantity",
                                "product_name": product_name,
                                "color": selected_color,
                                "size": selected_size,
                                "price": state["price"],
                            }))

                            send_instagram_message(sender_id,
                                f"📝 Great! You chose {selected_size} in {selected_color}.\nPlease enter quantity."
                            )
                            continue
                        else:
                            # ❌ Invalid size → stay in same step and show all available sizes for this color
                            size_resp = requests.post(
                                PRODUCT_SIZES_BY_COLOR_API_URL,
                                json={"title": product_name, "color": selected_color},
                                timeout=5
                            )
                            size_data = size_resp.json()
                            available_sizes = size_data.get("available_sizes", [])

                            send_instagram_message(sender_id,
                                f"❌ The size '{selected_size}' is not available for '{product_name}' in {selected_color}."
                            )

                            if available_sizes:
                                send_instagram_message(sender_id,
                                    "Available sizes:\n" + "\n".join(f"   - {s}" for s in available_sizes)
                                )
                                continue
                            else:
                                send_instagram_message(sender_id,
                                    f"No sizes are currently available for color '{selected_color}'."
                                )
                                continue

                    
                    # elif step == "awaiting_quantity":
                    #     # try:
                    #     #     qty = int(text.strip())
                    #     # except ValueError:
                    #     #     send_instagram_message(sender_id, "❌ Please enter a valid number for quantity.")
                    #     #     return

                    #     qty = query_ollama_quantity(text)
                    #     if qty <= 0:
                    #         send_instagram_message(sender_id, "❌ Please enter a valid quantity (like 1, 2, or 3).")
                    #         return


                    #     # product_name = state["product_name"]
                    #     # color = state.get("color", "")
                    #     # size = state.get("size", "")

                    #     # ✅ Move to confirmation
                    #     # r.set(f"user_state:{sender_id}", json.dumps({
                    #     #     "step": "awaiting_confirmation",
                    #     #     "product_name": product_name,
                    #     #     "color": color,
                    #     #     "size": size,
                    #     #     "quantity": qty,
                    #     #     "price": state["price"],
                    #     # }))

                    #     product_name = state["product_name"]
                    #     color = state.get("color", "")
                    #     size = state.get("size", "")
                    #     # qty = state["qty"]  
                    #     price = state["price"] 
                    #     save_order_to_db_all(sender_id, product_name, qty, price, qty * price, color=color, size=size)

                    #     r.delete(f"user_state:{sender_id}")

                    #     send_instagram_message(sender_id,
                    #         f"✅ You selected:\n"
                    #         f"Product: {product_name}\n"
                    #         f"Color: {color}\n"
                    #         f"Size: {size}\n"
                    #         f"Quantity: {qty}\n\n"
                    #         # "Do you wanna confirm your order ?"
                    #         "Your order is placed in cart . Feel free to continue Shopping. Let me know when you wanna checkout "
                    #     )
                    #     return

                    elif step == "awaiting_quantity":
                        qty = query_ollama_quantity(text)
                        if qty <= 0:
                            send_instagram_message(sender_id, "❌ Please enter a valid quantity (like 1, 2, or 3).")
                            return

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
                            send_instagram_message(sender_id, "⚠️ Couldn’t check stock right now. Please try again later.")
                            return

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
                                    send_instagram_message(
                                        sender_id,
                                        f"❌ Sorry, '{product_name}' ({color or 'N/A'}, {size or 'N/A'}) is currently out of stock."
                                    )
                                    r.delete(f"user_state:{sender_id}")
                                    return

                                if qty > available_stock:
                                    send_instagram_message(
                                        sender_id,
                                        f"⚠️ Only {available_stock} items available for '{product_name}' in {color or 'N/A'} {size or 'N/A'}.\n"
                                        f"Please enter a smaller quantity."
                                    )
                                    return
                            else:
                                pass

                        else:
                            # ✅ Product has NO attributes → allow order even if stock=0
                            # send_instagram_message(
                            #     sender_id,
                            #     f"ℹ️ '{product_name}' doesn’t have specific variants (like color or size), so stock is not restricted."
                            # )
                            pass

                        # ✅ Step 3: Save order
                        save_order_to_db_all(sender_id, product_name, qty, price, qty * price, color=color, size=size)
                        r.delete(f"user_state:{sender_id}")

                        send_instagram_message(sender_id,
                            f"✅ You selected:\n"
                            f"Product: {product_name}\n"
                            f"Color: {color or 'N/A'}\n"
                            f"Size: {size or 'N/A'}\n"
                            f"Quantity: {qty}\n\n"
                            "Your order is placed in cart. 🛒 Feel free to continue shopping. Let me know when you want to checkout!"
                        )
                        return



                    # elif step == "awaiting_confirmation" and text.lower() == "confirm":
                    # elif step == "awaiting_confirmation":
                    #     confirm_intent = query_ollama_confirmation(text_lower)
                    #     # Check if payment requires screenshot
                    #     if confirm_intent == "confirm_yes":
                    #         product_name = state["product_name"]
                    #         color = state.get("color", "")
                    #         size = state.get("size", "")
                    #         qty = state["quantity"]  
                    #         price = state["price"]  

                    #         # ✅ Save to DB
                    #         save_order_to_db_all(sender_id, product_name, qty, price, qty * price, color=color, size=size)

                    #         # ✅ Clear Redis state
                    #         r.delete(f"user_state:{sender_id}")
                    #         orders = get_pending_orders(sender_id)
                    #         if not orders:
                    #             send_instagram_message(sender_id, "You have no pending orders to confirm.")
                    #             continue
                    #         # Initialize multi-step flow in Redis
                    #         state = {
                    #                 "step": "asking_name",
                    #                 "customer_details": {"name": None, "address": None, "phone": None},
                    #                 "orders": orders,
                    #                 "payment": {"mode": None, "image": None}  # new field
                    #             }
                    #             # ✅ Use custom converter to handle Decimal
                    #         r.set(f"confirm_order:{sender_id}", json.dumps(state, default=decimal_default))
                    #         send_instagram_message(sender_id, "Sure! Let's confirm your order. Please tell me your full name.")
                    #         return
                        
                    #     elif confirm_intent == "confirm_no":
                    #         # cancel_pending_orders(sender_id)
                    #         r.delete(f"user_state:{sender_id}")
                    #         # state = None  # <-- reset local memory state
                    #         send_instagram_message(sender_id, "🛑 Your order has been cancelled. You can start a new order anytime.")
                    #         return                        
                    #     else:
                    #         send_instagram_message(sender_id, "You need to confirm your order to get to the next step. Would you like to confirm ?")
                    #         return



                # elif "attachments" in message.get("message", {}):

                #     print("attachement fr product search")
                #     for attachment in message["message"]["attachments"]:
                #         if attachment["type"] == "image":
                #             image_url = attachment["payload"]["url"]
                #             from .image import handle_instagram_image
                #             handle_instagram_image(sender_id, image_url)
                #             continue

                text_lower = text.lower()

                negative_intent = contains_pattern(text_lower, NEGATIVE_PATTERNS)
                intent = None          # ✅ ADD THIS
                llama_intent = None    # ✅ ADD THIS
                if negative_intent:
                    continue

                greetings = ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "yes"]
                num = is_number_only(text)
                context = ""
                pending_action = False

                if text_lower == "confirm":
                    intent = "confirm_order"
                    llama_intent = {"intent": "confirm_order", "negative_intent": False}

                elif num is not None:
                    # Force intent = place_quantity without asking Ollama
                    llama_intent = {
                        "intent": "place_quantity",
                        "category_filter": None,
                        "order_items": [
                            {"product": None, "quantity": num, "color": None, "size": None,
                            "gender": None, "style": None, "season": None, "fit": None,
                            "missing_slots": []}
                        ],
                        "customer_details": {"name": None, "address": None, "phone": None},
                        "negative_intent": False
                    }
                    intent = "place_quantity"
                elif r.get(f"user:{sender_id}:pending_action"):
                    pending_raw = r.get(f"user:{sender_id}:pending_action")
                    print("I am inside pending raw")
                    pending = json.loads(pending_raw)
                    action = pending.get("action")
                    product = pending.get("product")

                    print("text_lower in pending action", text_lower)

                    print("action from pending", action)

                    # 2️⃣ If bot was expecting product confirmation
                    if action == "confirm_product_order":

                        confirm_intent = query_ollama_confirmation(text_lower)
                        # if text_lower in AFFIRMATIVE:
                        if confirm_intent == "confirm_yes":
                            print("I am inside pending raw affirmative")
                            # ✅ Convert to place_order intent
                            r.delete(f"user:{sender_id}:pending_action")

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
                            # }
                        # elif text_lower in NEGATIVE:
                        elif confirm_intent == "confirm_no":
                            print("I am inside pending raw negative")
                            r.delete(f"user:{sender_id}:pending_action")
                            # return {"intent": "cancel_order"}
                            llama_intent = {
                                "intent": "cancel_order"
                            }
                            intent = "cancel_order"
                            pending_action = True

                        else:
                            # user typed something else → ask again
                            send_instagram_message(sender_id, "I couldn't understand you .Please reply yes or no.")
                            pending_action = True
                            continue

                # 3️⃣ Checking pending action for confirmation
                
                elif r.get(f"user:{sender_id}:pending_show_products"):
                    pending_show_product = r.get(f"user:{sender_id}:pending_show_products")
                    pending = json.loads(pending_show_product)
                    action = pending.get("action")

                    if action == "awaiting_product_confirmation":
                            # Ask LLaMA to interpret user's yes/no
                        confirm_intent = query_ollama_confirmation(text_lower)


                        print("confirm_intent from show products", confirm_intent)
                        if confirm_intent == "confirm_yes":
                            r.delete(f"user:{sender_id}:pending_show_products")

                            print("")
                            intent = "show_products"
                            llama_intent = {"intent": "show_products", "category_filter": None, "product_name": None, "negative_intent": False}
                                # send_instagram_message(sender_id, "Great! Here are our products for you to choose from...")
                                # Call your product listing function here
                        elif confirm_intent == "confirm_no":
                            r.delete(f"user:{sender_id}:pending_show_products")
                            send_instagram_message(sender_id, "No worries! Let me know if you change your mind.")
                            continue
                        else:
                            print("from show products")
                            send_instagram_message(sender_id, "Please reply yes or no.")
                            continue

                elif text_lower in greetings and not pending_action:

                    print("text lower from greetings", text_lower)
                    intent = "show_products"
                    llama_intent = {"intent": "show_products", "category_filter": None, "product_name": None, "negative_intent": False}



                # elif text_lower == "confirm":
                #     intent = "confirm_order"
                #     llama_intent = {"intent": "confirm_order", "negative_intent": False}
                # elif num is not None:
                #     # Force intent = place_quantity without asking Ollama
                #     llama_intent = {
                #         "intent": "place_quantity",
                #         "category_filter": None,
                #         "order_items": [
                #             {"product": None, "quantity": num, "color": None, "size": None,
                #             "gender": None, "style": None, "season": None, "fit": None,
                #             "missing_slots": []}
                #         ],
                #         "customer_details": {"name": None, "address": None, "phone": None},
                #         "negative_intent": False
                #     }
                #     intent = "place_quantity"
                else:
                    llama_intent_raw = query_ollama(text, context)
                    llama_intent = llama_intent_raw
                    # llama_intent = json.loads(llama_intent_raw)
                    intent = "could not understand"

                if not is_duplicate_message(sender_id, message_id, intent):
                    save_processed_message(sender_id, message_id, intent)
                else:
                    print(f"Skipping duplicate message: {message_id}")

        

                intent = llama_intent.get("intent", "none")
                # --- Mark this message as processed ---
                # save_processed_message(sender_id, message_id, intent)
                category_filter = llama_intent.get("category_filter")
                negative_intent = llama_intent.get("negative_intent", False)
                product_name = llama_intent.get("product_name")
                print("intent", intent)
                # print("category_filter",category_filter)
                # print("negative_intent", negative_intent)
                # print("type of category_filter", type(category_filter))
                # print("type of product_name", product_name)

                # --- Fetch all categories ---
                resp = requests.get(CATEGORIES_API_URL, timeout=5)
                all_categories = resp.json() if resp.status_code == 200 else []

                # print("api called")

                if intent == "small_talk":
                    send_instagram_message(sender_id, "Hello! Would you like to view our products for order?")

                    # Save pending action in Redis
                    r.setex(
                        f"user:{sender_id}:pending_show_products",
                        120,  # 1 hour expiry
                        json.dumps({
                            "action": "awaiting_product_confirmation"
                        })
                    )
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

                # elif intent == "place_order":
                #     print("inside place order from yes")
                #     order_items = llama_intent.get("order_items", [])

                #     print("order items", order_items)
                #     if order_items and len(order_items) > 0:
                #         try:
                #             # 🔹 Fetch all categories and their products
                #             category_resp = requests.get(
                #                 CATEGORIES_API_URL,
                #                 timeout=5
                #             )
                #             categories = category_resp.json() if category_resp.status_code == 200 else []

                #             # print("categories fteched")

                #             print("len", len(order_items))
                #             #   Flatten products into dict and list for embeddings
                #             PRODUCTS_LOOKUP = {}
                #             PRODUCTS_LIST = []
                #             for cat in categories:
                #                 for p in cat.get("products", []):
                #                     PRODUCTS_LIST.append(p["title"])
                #                     PRODUCTS_LOOKUP[p["title"]] = p
                #             # print("products lookup", PRODUCTS_LOOKUP)
                #             # print("products list",PRODUCTS_LIST)
                #             # Create embeddings for all products (you can cache this for performance)
                #             PRODUCT_EMBEDS = embedder.encode(PRODUCTS_LIST, convert_to_tensor=True)

                #             for item in order_items:
                #                 product_name_input = item.get("product")
                #                 # print("product name input", product_name_input)
                #                 quantity = item.get("quantity", 0)

                #                 if quantity == None:
                #                     # quantity = 1
                #                     quantity = 0
                #                 # print("quantity", quantity)
                #                 # if not product_name_input or not quantity:
                #                 #     continue
                #                 if not product_name_input:
                #                     continue

                #                 # 🔹 Embed user input
                #                 user_embed = embedder.encode(product_name_input, convert_to_tensor=True)

                #                 # 🔹 Find closest product using cosine similarity
                #                 cos_scores = util.cos_sim(user_embed, PRODUCT_EMBEDS)[0]
                #                 best_idx = torch.argmax(cos_scores).item()
                #                 similarity = cos_scores[best_idx].item()
                #                 matched_product_name = PRODUCTS_LIST[best_idx]

                #                 # print("matched_product name before", matched_product_name)


                #                 # matched_product_name, similarity = find_closest_product_faiss(product_name_input)
                #                 print("similarity", similarity)
                                                                
                #                 if similarity < 0.98:
                #                     # Find top N similar products (not just the best one)
                #                     cos_scores_list = cos_scores.tolist()
                #                     top_indices = sorted(range(len(cos_scores_list)), key=lambda i: cos_scores_list[i], reverse=True)[:5]

                #                     quick_replies = []
                #                     for idx in top_indices:
                #                         candidate_name = PRODUCTS_LIST[idx]
                #                         quick_replies.append({
                #                             "content_type": "text",
                #                             "title": candidate_name[:20],  # max 20 chars for Instagram
                #                             "payload": f"PRODUCT_{candidate_name.upper().replace(' ', '_')}"
                #                         })

                #                     payload = {
                #                         "recipient": {"id": sender_id},
                #                         "message": {
                #                             "text": f"🤔 We found multiple products similar to '{product_name_input}'. Please choose one:",
                #                             "quick_replies": quick_replies
                #                         }
                #                     }

                #                     headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
                #                     requests.post(GRAPH_API_URL, headers=headers, json=payload)
                #                     continue  # skip further processing until user selects a product

                #                 # print("matched_product name after", matched_product_name)
                #                 matched_product = PRODUCTS_LOOKUP[matched_product_name]


                #                 is_promo = matched_product["is_promo"]
                #                 price = float(matched_product["price"])

                #                 if is_promo:
                #                     price = float(matched_product.get("promo_price", 0.0))
                #                 # 🔹 Calculate total price
                #                 total_price = price * int(quantity)

                #                 # 🔹 Save order
                #                 save_order_to_db_all(sender_id, matched_product["title"], quantity, price, total_price)

                #                 # 🔹 Fetch pending order to check attributes
                #                 connection = get_db_connection()
                #                 cursor = connection.cursor(dictionary=True)
                #                 cursor.execute("""
                #                     SELECT id, product_name FROM orders
                #                     WHERE sender_id = %s AND status = 'pending' AND attributes_filled = FALSE
                #                     ORDER BY id ASC LIMIT 1
                #                 """, (sender_id,))
                #                 pending_order = cursor.fetchone()
                #                 pending_product_name = pending_order["product_name"] if pending_order else matched_product_name

                #                 # 🔹 Fetch product attributes if any
                #                 attr_resp = requests.post(
                #                     PRODUCT_ATTRIBUTES_API_URL,
                #                     json={"product_name": pending_product_name},
                #                     timeout=5
                #                 )
                #                 attr_data = attr_resp.json() if attr_resp.status_code == 200 else {}
                #                 attributes = attr_data.get("attributes", {})

                #                 color_options = attributes.get("color", [])
                #                 size_options = attributes.get("size", [])

                #                 if color_options or size_options:
                #                     attr_message = f"⚙️ Please specify the missing details for your product {pending_product_name}:\n\n"
                #                     if size_options:
                #                         attr_message += "📏 *Available Sizes:*\n" + "\n".join(f"   - {s}" for s in size_options)
                #                     if color_options:
                #                         attr_message += "\n\n🎨 *Available Colors:*\n" + "\n".join(f"   - {c}" for c in color_options)
                #                     attr_message += (
                #                         "\n\n📝 *Reply in this format:*\n"
                #                         "👉 `XL Golden, L Blue, XXL Red`\n\n"
                #                         "Each item should include size and color if applicable."
                #                     )
                #                     send_instagram_message(sender_id, attr_message)

                #                 else:
                #                     # ✅ No attributes → now check for quantity requirement
                #                     cursor.execute("""
                #                         SELECT id, product_name FROM orders
                #                         WHERE sender_id = %s AND status='pending' AND quantity=0
                #                         ORDER BY id ASC LIMIT 1
                #                     """, (sender_id,))
                #                     pending_qty = cursor.fetchone()

                #                     cursor.close()
                #                     connection.close()

                #                     if pending_qty:
                #                         # Ask quantity for only the FIRST pending product
                #                         send_instagram_message(
                #                             sender_id,
                #                             f"📝 Please enter quantity for: *{pending_qty['product_name']}*\n"
                #                             "Reply with a number like:\n"
                #                             "👉 1, 2, 3, four, ten"
                #                         )
                #                     else:
                #                         # If quantity is already known → proceed to confirmation
                #                         send_instagram_message(
                #                             sender_id,
                #                             f"✅ Added {quantity} x {matched_product['title']} (Rs {price} each).\n"
                #                             f"Total: Rs {total_price}\n\n"
                #                             "🎉 All items ready!\n"
                #                             "To confirm delivery, reply:\n"
                #                             "Confirm"
                                            
                #                         )

                #         except Exception as e:
                #             print("⚠️ Order processing failed:", e)
                #             send_instagram_message(sender_id, "❌ Something went wrong while processing your order. Please try again.")

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

                            # print("categories fteched")

                            print("len", len(order_items))
                            #   Flatten products into dict and list for embeddings
                            PRODUCTS_LOOKUP = {}
                            PRODUCTS_LIST = []
                            for cat in categories:
                                for p in cat.get("products", []):
                                    PRODUCTS_LIST.append(p["title"])
                                    PRODUCTS_LOOKUP[p["title"]] = p
                            # print("products lookup", PRODUCTS_LOOKUP)
                            # print("products list",PRODUCTS_LIST)
                            # Create embeddings for all products (you can cache this for performance)
                            PRODUCT_EMBEDS = embedder.encode(PRODUCTS_LIST, convert_to_tensor=True)

                            for item in order_items:
                                product_name_input = item.get("product")
                                # print("product name input", product_name_input)
                                quantity = item.get("quantity", 0)

                                if quantity == None:
                                    # quantity = 1
                                    quantity = 0
                                # print("quantity", quantity)
                                # if not product_name_input or not quantity:
                                #     continue
                                if not product_name_input:
                                    continue

                                # 🔹 Embed user input
                                user_embed = embedder.encode(product_name_input, convert_to_tensor=True)

                                # 🔹 Find closest product using cosine similarity
                                cos_scores = util.cos_sim(user_embed, PRODUCT_EMBEDS)[0]
                                best_idx = torch.argmax(cos_scores).item()
                                similarity = cos_scores[best_idx].item()
                                matched_product_name = PRODUCTS_LIST[best_idx]

                                # print("matched_product name before", matched_product_name)


                                # matched_product_name, similarity = find_closest_product_faiss(product_name_input)
                                print("similarity", similarity)
                                                                
                                if similarity < 0.98:
                                    # Find top N similar products (not just the best one)
                                    cos_scores_list = cos_scores.tolist()
                                    top_indices = sorted(range(len(cos_scores_list)), key=lambda i: cos_scores_list[i], reverse=True)[:5]

                                    quick_replies = []
                                    for idx in top_indices:
                                        candidate_name = PRODUCTS_LIST[idx]
                                        quick_replies.append({
                                            "content_type": "text",
                                            "title": candidate_name[:20],  # max 20 chars for Instagram
                                            "payload": f"PRODUCT_{candidate_name.upper().replace(' ', '_')}"
                                        })

                                    payload = {
                                        "recipient": {"id": sender_id},
                                        "message": {
                                            "text": f"🤔 We found multiple products similar to '{product_name_input}'. Please choose one:",
                                            "quick_replies": quick_replies
                                        }
                                    }

                                    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
                                    requests.post(GRAPH_API_URL, headers=headers, json=payload)
                                    continue  # skip further processing until user selects a product

                                # print("matched_product name after", matched_product_name)
                                matched_product = PRODUCTS_LOOKUP[matched_product_name]


                                is_promo = matched_product["is_promo"]
                                price = float(matched_product["price"])

                                if is_promo:
                                    price = float(matched_product.get("promo_price", 0.0))
                                # 🔹 Calculate total price
                                total_price = price * int(quantity)

                                # 🔹 Save order
                                # save_order_to_db_all(sender_id, matched_product["title"], quantity, price, total_price)

                                # 🔹 Fetch pending order to check attributes
                                # connection = get_db_connection()
                                # cursor = connection.cursor(dictionary=True)
                                # cursor.execute("""
                                #     SELECT id, product_name FROM orders
                                #     WHERE sender_id = %s AND status = 'pending' AND attributes_filled = FALSE
                                #     ORDER BY id ASC LIMIT 1
                                # """, (sender_id,))
                                # pending_order = cursor.fetchone()
                                # pending_product_name = pending_order["product_name"] if pending_order else matched_product_name

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

                                # if color_options or size_options:
                                #     attr_message = f"⚙️ Please specify the missing details for your product {pending_product_name}:\n\n"
                                #     if size_options:
                                #         attr_message += "📏 *Available Sizes:*\n" + "\n".join(f"   - {s}" for s in size_options)
                                #     if color_options:
                                #         attr_message += "\n\n🎨 *Available Colors:*\n" + "\n".join(f"   - {c}" for c in color_options)
                                #     attr_message += (
                                #         "\n\n📝 *Reply in this format:*\n"
                                #         "👉 `XL Golden, L Blue, XXL Red`\n\n"
                                #         "Each item should include size and color if applicable."
                                #     )
                                #     send_instagram_message(sender_id, attr_message)

                                # else:
                                #     # ✅ No attributes → now check for quantity requirement
                                #     cursor.execute("""
                                #         SELECT id, product_name FROM orders
                                #         WHERE sender_id = %s AND status='pending' AND quantity=0
                                #         ORDER BY id ASC LIMIT 1
                                #     """, (sender_id,))
                                #     pending_qty = cursor.fetchone()

                                #     cursor.close()
                                #     connection.close()

                                #     if pending_qty:
                                #         # Ask quantity for only the FIRST pending product
                                #         send_instagram_message(
                                #             sender_id,
                                #             f"📝 Please enter quantity for: *{pending_qty['product_name']}*\n"
                                #             "Reply with a number like:\n"
                                #             "👉 1, 2, 3, four, ten"
                                #         )
                                #     else:
                                #         # If quantity is already known → proceed to confirmation
                                #         send_instagram_message(
                                #             sender_id,
                                #             f"✅ Added {quantity} x {matched_product['title']} (Rs {price} each).\n"
                                #             f"Total: Rs {total_price}\n\n"
                                #             "🎉 All items ready!\n"
                                #             "To confirm delivery, reply:\n"
                                #             "Confirm"
                                            
                                #         )

                                if color_options:
                                    # Save in Redis that we are waiting for color selection
                                    r.set(f"user_state:{sender_id}", json.dumps({
                                        "step": "awaiting_color",
                                        "product_name": matched_product["title"],
                                        "price": price  # ✅ store here
                                    }))
                                    # send_instagram_message(sender_id, 
                                    #     f"🎨 Please select a color for {matched_product["title"]}:\n" +
                                    #     "\n".join(f"   - {c}" for c in color_options)
                                    # )

                                    # Build a nice formatted message
                                    color_list = "\n".join(f"• {c.capitalize()}" for c in color_options)
                                    message_text = (
                                        f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                        f"🎨 Available colors:\n{color_list}\n\n"
                                        f"👉 Please reply with your preferred color name to continue."
                                    )

                                    send_instagram_message(sender_id, message_text)

                                elif size_options:
                                    r.set(f"user_state:{sender_id}", json.dumps({
                                        "step": "awaiting_size",
                                        "product_name": matched_product["title"],
                                        "price": price
                                    }))

                                    size_list = "\n".join(f"• {s.capitalize()}" for s in size_options)
                                    message_text = (
                                        f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                        f"📏 Available sizes:\n{size_list}\n\n"
                                        f"👉 Please reply with your preferred size to continue."
                                    )
                                    send_instagram_message(sender_id, message_text)

                                else:
                                    r.set(f"user_state:{sender_id}", json.dumps({
                                        "step": "awaiting_quantity",
                                        "product_name": matched_product["title"],
                                        "price": price
                                    }))

                                    message_text = (
                                        f"🛍️ Great choice! You selected *{matched_product['title']}*.\n\n"
                                        f"🧮 Please reply with the quantity you'd like to order.\n"
                                        f"👉 Example: `1`, `2`, or `3`"
                                    )
                                    send_instagram_message(sender_id, message_text)

                        except Exception as e:
                            print("⚠️ Order processing failed:", e)
                            send_instagram_message(sender_id, "❌ Something went wrong while processing your order. Please try again.")


                elif intent == "place_quantity":
                    print("got inside place quantity")
                    order_items = llama_intent.get("order_items", [])
                    sender = sender_id

                    if order_items:
                        qty = order_items[0]["quantity"]
                        print("quantity from place quantity intent", qty)

                        connection = get_db_connection()
                        cursor = connection.cursor(dictionary=True)

                        # Find the first pending product with missing quantity
                        cursor.execute("""
                            SELECT id, product_name FROM orders
                            WHERE sender_id = %s AND status='pending' AND quantity=0
                            ORDER BY id ASC LIMIT 1
                        """, (sender,))
                        pending_order = cursor.fetchone()

                        if pending_order:
                            # Get price
                            cursor.execute("SELECT price FROM orders WHERE id=%s", (pending_order["id"],))
                            row = cursor.fetchone()
                            price = float(row["price"])

                            # Calculate total
                            total_price = price * qty

                            # Update both quantity and total_price
                            cursor.execute("""
                                UPDATE orders SET quantity=%s, total_price=%s WHERE id=%s
                            """, (qty, total_price, pending_order["id"]))
                            connection.commit()

                            # Check if another item still needs quantity
                            cursor.execute("""
                                SELECT id, product_name FROM orders
                                WHERE sender_id = %s AND status='pending' AND quantity=0
                                ORDER BY id ASC LIMIT 1
                            """, (sender,))
                            next_pending = cursor.fetchone()

                            if next_pending:
                                send_instagram_message(
                                    sender_id,
                                    f"✅ Quantity set for {pending_order['product_name']}.\n"
                                    f"📝 Now, enter quantity for: *{next_pending['product_name']}*"
                                )
                            else:
                                send_instagram_message(
                                    sender_id,
                                    "✅ All quantities are set!\n"
                                    "🎉 Your order is ready.\n"
                                    "To confirm delivery, reply:\n"
                                    "Confirm"
                                )

                        cursor.close()
                        connection.close()

                elif intent == "check_out":
                    print("checkout_intent", intent)
                    orders = get_pending_orders(sender_id)
                    if not orders:
                        send_instagram_message(sender_id, "You have no orders in cart to checkout")
                        continue
                    print("orders", orders)
                    print("I am in confirm order")
                    # Check if the user already has a state in Redis
                    state_json = r.get(f"confirm_order:{sender_id}")
                    # r.delete(f"confirm_order:{sender_id}")

                    print("state_json", state_json)
                    if not state_json:
                        # Initialize multi-step flow in Redis
                        state = {
                            "step": "asking_name",
                            "customer_details": {"name": None, "address": None, "phone": None},
                            "orders": orders,
                            "payment": {"mode": None, "image": None}  # new field
                        }
                        # ✅ Use custom converter to handle Decimal
                        r.set(f"confirm_order:{sender_id}", json.dumps(state, default=decimal_default))
                        send_instagram_message(sender_id, "Sure! Let's confirm your order. Please tell me your full name.")
                        continue

                    print("I am in check-out order . no condition satisfied")

                elif intent == "add_attribute":
                    order_items = llama_intent.get("order_items", [])
                    print("addd_attribute order items", order_items)
                    sender = sender_id

                    if order_items:
                        connection = get_db_connection()
                        cursor = connection.cursor(dictionary=True)

                        # Step 0: Check if negative sales is allowed
                        try:
                            negative_sales_resp = requests.get(
                                NEGATIVE_SALES_API_URL,
                                timeout=5
                            )
                            negative_sales_data = negative_sales_resp.json() if negative_sales_resp.status_code == 200 else {}
                            allow_negative_sales = negative_sales_data.get("allow_negative_sales", False)
                        except Exception as e:
                            print("⚠️ Failed to fetch negative sales flag:", e)
                            allow_negative_sales = False

                        # Find the oldest order that still needs attributes
                        cursor.execute("""
                            SELECT id, product_name, quantity FROM orders
                            WHERE sender_id = %s AND status = 'pending' AND attributes_filled = FALSE
                            ORDER BY id ASC LIMIT 1
                        """, (sender,))
                        current_order = cursor.fetchone()

                        if current_order:
                            item = order_items[0]  # Take first product’s attributes
                            color = item.get("color")
                            size = item.get("size")

                            # Step 1: Check stock for the chosen variant
                            try:
                                stock_resp = requests.post(
                                    STOCKBYNAME_API_URL,
                                    json={
                                        "product_name": current_order["product_name"],
                                        "color": color or "",
                                        "size": size or "",
                                        "gender": "",
                                        "style": "",
                                        "fit": "",
                                        "season": ""
                                    },
                                    timeout=5
                                )
                                stock_data = stock_resp.json() if stock_resp.status_code == 200 else {}

                                stock_qty = stock_data.get("stock_quantity", 0)
                                available_price = stock_data.get("price", 0)

                                # Step 2: Check stock availability or allow negative sales
                                if stock_qty <= 0 and not allow_negative_sales:
                                    send_instagram_message(
                                        sender_id,
                                        f"❌ Sorry, {current_order['product_name']} ({size}, {color}) is currently out of stock."
                                    )
                                    cursor.close()
                                    connection.close()
                                    continue

                                elif stock_qty < (current_order.get("quantity") or 1) and not allow_negative_sales:
                                    send_instagram_message(
                                        sender_id,
                                        f"⚠️ Sorry, only {stock_qty} left in stock for {current_order['product_name']} ({size}, {color})."
                                    )
                                    cursor.close()
                                    connection.close()
                                    continue

                                # Step 3: Stock available (or negative sales allowed) → update the order
                                cursor.execute("""
                                    UPDATE orders
                                    SET color=%s, size=%s, attributes_filled=TRUE
                                    WHERE id=%s
                                """, (color, size, current_order["id"]))
                                connection.commit()

                            except Exception as e:
                                print("⚠️ Stock check failed:", e)
                                send_instagram_message(sender_id, "Couldn't verify stock at the moment. Please try again.")
                                cursor.close()
                                connection.close()
                                continue

                            # Step 4: Check for next pending item
                            cursor.execute("""
                                SELECT id, product_name FROM orders
                                WHERE sender_id = %s AND status='pending' AND attributes_filled=FALSE
                                ORDER BY id ASC LIMIT 1
                            """, (sender,))
                            next_order = cursor.fetchone()

                            if next_order:
                                next_product_name = next_order["product_name"]

                                try:
                                    # Fetch next product’s available attributes
                                    attr_resp = requests.post(
                                        PRODUCT_ATTRIBUTES_API_URL,
                                        json={"product_name": next_product_name},
                                        timeout=5
                                    )
                                    attr_data = attr_resp.json() if attr_resp.status_code == 200 else {}
                                    attributes = attr_data.get("attributes", {})

                                    color_options = attributes.get("color", [])
                                    size_options = attributes.get("size", [])

                                    if color_options or size_options:
                                        attr_message = (
                                            f"✅ Updated attributes for {current_order['product_name']}.\n\n"
                                            f"⚙️ Now, please specify the missing details for your next product: *{next_product_name}*\n\n"
                                        )

                                        if size_options:
                                            attr_message += "📏 *Available Sizes:*\n"
                                            for s in size_options:
                                                attr_message += f"   - {s}\n"

                                        if color_options:
                                            attr_message += "\n🎨 *Available Colors:*\n"
                                            for c in color_options:
                                                attr_message += f"   - {c}\n"

                                        attr_message += (
                                            "\n📝 *Reply in this format:*\n"
                                            "👉 `XL Golden, L Blue, XXL Red`\n\n"
                                            "Each item should include size and color if applicable."
                                        )

                                        send_instagram_message(sender_id, attr_message)
                                        continue
                                    else:

                                        cursor.execute("""
                                            SELECT id, product_name FROM orders
                                            WHERE sender_id = %s AND status='pending' AND quantity=0
                                            ORDER BY id ASC LIMIT 1
                                        """, (sender,))
                                        pending_order = cursor.fetchone()

                                        if pending_order:
                                            # Prepare a message listing all products with missing quantity
                                            send_instagram_message(
                                                sender_id,
                                                f"✅ Updated attributes for {current_order['product_name']}.\n"
                                                f"Next product: {next_product_name} has no size/color options.\n"
                                                f"Please enter the quantities for the following products:\n{pending_order['product_name']}\n\n"
                                                "Reply in this format:\n"
                                                "👉 1 , 2, one , two"
                                            )
                                        else:
                                            # If all quantities are already set, proceed with confirmation
                                            send_instagram_message(
                                                f"✅ Updated attributes for {current_order['product_name']}.\n"
                                                f"Next product: {next_product_name} has no size/color options.\n"
                                                "🎉 Your order is ready!\n"
                                                "To confirm delivery, reply:\n"
                                                "Confirm"
                                            )
                                        # send_instagram_message(
                                        #     sender_id,
                                        #     f"✅ Updated attributes for {current_order['product_name']}.\n"
                                        #     f"Next product: {next_product_name} has no size/color options.\n"
                                        #     "Please confirm your order using:\n"
                                        #     "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                                        # )
                                        continue
                                except Exception as e:
                                    print("⚠️ Attribute fetch failed:", e)

                                    cursor.execute("""
                                        SELECT id, product_name FROM orders
                                        WHERE sender_id = %s AND status='pending' AND quantity=0 ORDER BY id ASC LIMIT 1
                                    """, (sender,))
                                    pending_order = cursor.fetchone()

                                    if pending_order:
                                        # Prepare a message listing all products with missing quantity
                                        send_instagram_message(
                                            sender_id,
                                            f"✅ Updated attributes for {current_order['product_name']}.\n"
                                            f"Couldn't fetch attributes for {next_product_name}."
                                            f"Please enter the quantities for the following products:\n{pending_order['product_name']}\n\n"
                                            "Reply in this format:\n"
                                            "👉 1 , 2, one , two"
                                        )
                                    else:
                                        # If all quantities are already set, proceed with confirmation
                                        send_instagram_message(
                                            sender_id,
                                            f"✅ Updated attributes for {current_order['product_name']}.\n"
                                            f"Couldn't fetch attributes for {next_product_name}."
                                            "🎉 Your order is ready!\n"
                                            "To confirm delivery, reply:\n"
                                            "Confirm"
                                        )
                                    # send_instagram_message(
                                    #     sender_id,
                                    #     f"✅ Updated attributes for {current_order['product_name']}.\n"
                                    #     f"Couldn't fetch attributes for {next_product_name}. Please specify manually."
                                    # )
        
                                    continue
                            else:

                                cursor.execute("""
                                    SELECT id, product_name FROM orders
                                    WHERE sender_id = %s AND status='pending' AND quantity=0 ORDER BY id ASC LIMIT 1
                                """, (sender,))
                                pending_order = cursor.fetchone()

                                if pending_order:
                                    # Prepare a message listing all products with missing quantity
                                    send_instagram_message(
                                        sender_id,
                                        f"✅ All attributes have been updated.\n\n"
                                        f"Please enter the quantities for the following products:\n{pending_order['product_name']}\n\n"
                                        "Reply in this format:\n"
                                        "👉 1 , 2, one , two"
                                    )
                                else:
                                    # If all quantities are already set, proceed with confirmation
                                    send_instagram_message(
                                        sender_id,
                                        "✅ All attributes and quantities are set.\n"
                                        "🎉 Your order is ready!\n"
                                        "To confirm delivery, reply:\n"
                                        "Confirm"
                                    )
                                # send_instagram_message(
                                #     sender_id,
                                #     f"✅ Attributes updated for {current_order['product_name']}.\n"
                                #     "🎉 All items are ready! Please confirm your order using:\n"
                                #     "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                                # )
                                continue
                        else:
                            send_instagram_message(sender_id, "No pending order found needing attributes.")
                            continue

                # elif intent == "confirm_order":
                #     orders = get_pending_orders(sender_id)
                #     if not orders:
                #         send_instagram_message(sender_id, "You have no pending orders to confirm.")
                #         continue

                #     customer_details = llama_intent.get("customer_details", {})
                #     name = customer_details.get("name")
                #     address = customer_details.get("address")
                #     phone = customer_details.get("phone")

                #     print("name", name)
                #     print("address", address)
                #     print("phone", phone)

                #     if not name or not phone:
                #         send_instagram_message(
                #             sender_id,
                #             "Please provide your Name, Address, and Phone in this format:\n"
                #             "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                #         )
                #         continue

                #     # Call delivery API
                #     response, total_amount = create_delivery(orders, customer_details)

                #     if response.status_code in [200, 201]:
                #         # Update order status in DB
                #         for order in orders:
                #             update_order_status(order["id"], "confirmed")

                #         send_instagram_message(
                #             sender_id,
                #             f"✅ Your order has been confirmed and delivery created!\n"
                #             f"Total: Rs {total_amount}\n"
                #             f"Name: {name}, Address: {address}, Phone: {phone}"
                #         )
                #     else:
                #         send_instagram_message(sender_id, "❌ Failed to create delivery. Please try again.")
                #     continue

                elif intent == "confirm_order":
                    orders = get_pending_orders(sender_id)
                    if not orders:
                        send_instagram_message(sender_id, "You have no pending orders to confirm.")
                        continue
                    print("orders", orders)
                    print("I am in confirm order")
                    # Check if the user already has a state in Redis
                    state_json = r.get(f"confirm_order:{sender_id}")
                    # r.delete(f"confirm_order:{sender_id}")

                    print("state_json", state_json)
                    if not state_json:
                        # Initialize multi-step flow in Redis
                        state = {
                            "step": "asking_name",
                            "customer_details": {"name": None, "address": None, "phone": None},
                            "orders": orders,
                            "payment": {"mode": None, "image": None}  # new field
                        }
                        # ✅ Use custom converter to handle Decimal
                        r.set(f"confirm_order:{sender_id}", json.dumps(state, default=decimal_default))
                        send_instagram_message(sender_id, "Sure! Let's confirm your order. Please tell me your full name.")
                        continue

                    print("I am in confirm order . no condition satisfied")

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
                    print(intent)
                    # response = "Sorry, I couldn’t process your message right now."

                    # send_instagram_message(sender_id, response)
                    print("💬 LLM failed → Applying fuzzy fallback matching...")

                    # Fetch categories if not already fetched
                    all_categories = fetch_all_categories()  # <-- your existing function

                    matched_categories, matched_products = fallback_category_match(text_lower, all_categories)

                    # If nothing matched → truly no idea, return default
                    if not matched_categories and not matched_products:
                        send_instagram_message(sender_id, "I couldn't recognize the category. Please try with a different name 🙏")
                        return {"status": "ok"}

                    # # If we got matched categories → show category quick replies (same UI as before)
                    # if matched_categories:
                    #     quick_replies = [{
                    #         "content_type": "text",
                    #         "title": cat['title'][:20],
                    #         "payload": f"CATEGORY_{cat['title'].upper().replace(' ', '_')}"
                    #     } for cat in matched_categories[:13]]

                    #     payload = {
                    #         "recipient": {"id": sender_id},
                    #         "message": {
                    #             "text": "Please select a category 👇",
                    #             "quick_replies": quick_replies
                    #         }
                    #     }

                    #     headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
                    #     requests.post(GRAPH_API_URL, headers=headers, json=payload)
                    #     return {"status": "ok"}

                    # If we got matched categories → directly show products of the best matched category
                    if matched_categories:
                        best_category = matched_categories[0]   # pick the top matched category
                        category_products = best_category.get("products", [])


                        print("I sent that category")
                        if category_products:
                            send_instagram_carousel_initial(sender_id, category_products)
                            return {"status": "ok"}
                        else:
                            send_instagram_message(sender_id, "This category has no products right now 🙏")
                            return {"status": "ok"}

                    # # If only products matched → send product carousel (same UI as before)
                    # if matched_products:
                    #     send_instagram_carousel_initial(sender_id, matched_products)
                    #     # You already do this in show_products for state:
                    #     PRODUCTS = {p['title'].lower(): p for p in matched_products}
                    #     return {"status": "ok"}


                    # If only products matched → send quick replies
                    if matched_products:
                        # Take top N products (say 5)
                        top_products = matched_products[:5]


                        print("top products", top_products)
                        quick_replies = []
                        for p in top_products:
                            product_title = p['title']
                            quick_replies.append({
                                "content_type": "text",
                                "title": product_title[:20],  # Instagram limit
                                "payload": f"PRODUCT_{product_title.upper().replace(' ', '_')}"
                            })
                        if len(matched_products) == 1:
                            prompt_text = f"🤔 We found a product that seems to match your need:\nPlease choose if this is the one you're looking for."
                        else:
                            prompt_text = f"🤔 We found multiple products similar to your search.\nPlease choose the one you are looking for from the list below:"
                        payload = {
                            "recipient": {"id": sender_id},
                            "message": {
                                # "text": f"🤔 We found multiple products similar to '{text_lower}'. Please choose one:",

                                "text": prompt_text,

                                "quick_replies": quick_replies
                            }
                        }

                        headers = {
                            "Authorization": f"Bearer {ACCESS_TOKEN}",
                            "Content-Type": "application/json"
                        }
                        requests.post(GRAPH_API_URL, headers=headers, json=payload)


                        return {"status": "ok"}

import requests

def get_payment_modes():
    url = "https://vibezdc.silverlinepos.com/api/payment-mode/"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print("Error fetching payment modes:", e)
    return []



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


# def save_order_to_db_all(sender_id, product_name, quantity, total_price, color=None, size=None, gender=None, style=None, season=None, fit=None, status="pending"):
#     """Save order details with variant info."""
#     connection = get_db_connection()
#     cursor = connection.cursor()
#     cursor.execute("""
#         INSERT INTO orders 
#         (sender_id, product_name, quantity, total_price, status, color, size, gender, style, season, fit)
#         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#     """, (sender_id, product_name, quantity, total_price, status, color, size, gender, style, season, fit))
#     connection.commit()
#     cursor.close()
#     connection.close()
def save_order_to_db_all(sender_id, product_name, quantity, price, total_price=0, color=None, size=None, gender=None, style=None, season=None, fit=None ):
    """Save order details with variant info."""
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("""
        INSERT INTO orders 
        (sender_id, product_name, quantity, total_price, price, status, color, size, gender, style, season, fit)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (sender_id, product_name, quantity, total_price, price, "pending", color, size, gender, style, season, fit))
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

def cancel_pending_orders(sender_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("""
        UPDATE orders
        SET status = 'cancelled'
        WHERE sender_id = %s AND status = 'pending'
    """, (sender_id,))
    connection.commit()
    cursor.close()
    connection.close()

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

def update_order_payment_mode(order_id, payment_mode):
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute("""
        UPDATE orders
        SET payment_mode = %s
        WHERE id = %s
    """, (payment_mode, order_id))
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
        # element = {
        #     "title": p["title"],
        #     "image_url": p.get("image"),
        #     "subtitle": f"Price: Rs {p['price']}",
        # }

        if p.get("is_promo"):
            original_price = p['price']
            promo_price = p.get("promo_price", original_price)
            price_text = f"This is a promotional product. Hence you can get it for: \n 💸 Rs {promo_price} (was Rs {original_price}) 🎉"
        else:
            price_text = f"Rs {p['price']}"

        element = {
            "title": p["title"],
            "image_url": p.get("image"),
            "subtitle": f"Price: {price_text}",
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


# def send_instagram_product_details(recipient_id, product):
#     # Send image
#     image_payload = {
#         "recipient": {"id": recipient_id},
#         "message": {
#             "attachment": {
#                 "type": "image",
#                 "payload": {"url": product["image"]}
#             }
#         }
#     }
#     headers = {
#         "Authorization": f"Bearer {ACCESS_TOKEN}",
#         "Content-Type": "application/json"
#     }
#     requests.post(GRAPH_API_URL, headers=headers, json=image_payload)

#     # Determine price display
#     if product.get("is_promo"):
#         original_price = product['price']
#         promo_price = product.get("promo_price", original_price)
#         price_text = f"~~Rs {original_price}~~ Rs {promo_price} 🎉"
#     else:
#         price_text = f"Rs {product['price']}"

#     # Send order prompt
#     order_payload = {
#         "recipient": {"id": recipient_id},
#         "message": {
#             "text": f"{product['title']} - {price_text}\nDo you want to order this?",
#             "quick_replies": [
#                 {
#                     "content_type": "text",
#                     "title": f"Order {product['title']}",
#                     "payload": f"ORDER_{product['title'].upper().replace(' ', '_')}"
#                 },
#                 {"content_type": "text", "title": "❌ Cancel", "payload": "CANCEL_ORDER"}
#             ]
#         }
#     }
#     requests.post(GRAPH_API_URL, headers=headers, json=order_payload)


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

    # Determine price display
    if product.get("is_promo"):
        original_price = product['price']
        promo_price = product.get("promo_price", original_price)
        price_text = f"~~Rs {original_price}~~ Rs {promo_price} 🎉"
    else:
        price_text = f"Rs {product['price']}"

    # Send order prompt
    order_payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": f"{product['title']} - {price_text}\nDo you want to order this?",
            "quick_replies": [
                {"content_type": "text", "title": f"Order {product['title']}", "payload": f"ORDER_{product['title'].upper().replace(' ', '_')}"},
                {"content_type": "text", "title": "❌ Cancel", "payload": "CANCEL_ORDER"}
            ]
        }
    }
    requests.post(GRAPH_API_URL, headers=headers, json=order_payload)

    # ✅ Save pending confirmation state to Redis
    r.setex(
        f"user:{recipient_id}:pending_action",
        120,  # 1 hour
        json.dumps({
            "action": "confirm_product_order",
            "product": product
        })
    )



def get_product_by_name(product_name: str):
    """
    Fetch product info from the API based on name.
    """
    try:
        resp = requests.get(CATEGORIES_API_URL, timeout=5)
        categories = resp.json() if resp.status_code == 200 else []

        for cat in categories:
            for prod in cat.get("products", []):
                if prod["title"].lower() == product_name.lower():
                    return prod
    except Exception as e:
        print("⚠️ Error fetching product:", e)

    return None

# import requests
# import json
# from datetime import datetime

# # -------------------- DELIVERY API FUNCTION --------------------
# def create_delivery(orders, customer_details):
#     """
#     Post delivery to the delivery API.
#     orders: list of pending order dicts
#     customer_details: dict with keys name, address, phone
#     """
#     delivery_items = []
#     total_amount = 0

#     for order in orders:
#         product_name = order["product_name"].lower()
#         color = order["color"].lower()
#         size = order["size"].lower()
#         quantity = float(order["quantity"])
#         total_price = float(order["total_price"])
#         total_amount += total_price

#         # Match product ID from PRODUCTS dict (fetched previously from API)
#         product_info = next((p for p in PRODUCTS.values() if p["title"].lower() == product_name), None)
#         if not product_info:
#             continue

#         print(product_info)
#         delivery_items.append({
#             "product": product_info["id"],
#             "quantity": quantity,
#             "size": size,
#             "color": color,

#         })

#     # Generate bill_no using first order id or timestamp
#     bill_no = f"BIL-{orders[0]['id']}-{int(datetime.now().timestamp())}"

#     # Prepare payload
#     payload = {
#         "date": datetime.now().strftime("%Y-%m-%d"),
#         "time": datetime.now().strftime("%H:%M:%S"),
#         "deliveryDate": (datetime.now()).strftime("%Y-%m-%dT%H:%M:%SZ"),
#         "deliver_to": customer_details.get("address"),
#         "special_request": "",
#         "Current_state": "Ordered",
#         "delivery_option": "Express",
#         "bill_no": bill_no,
#         "order_type": "Instagram",
#         "customer": {
#             "name": customer_details.get("name"),
#             "tax_number": "",
#             "address": customer_details.get("address"),
#             "contact_number": customer_details.get("phone"),
#             "email": "",
#             "branch": None
#         },
#         "delivery_details": delivery_items
#     }

#     # Post to API
#     api_url = "https://vibezdc.silverlinepos.com/api/delivery-create/"
#     headers = {"Content-Type": "application/json"}
#     response = requests.post(api_url, headers=headers, json=payload)
#     return response, total_amount


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

    # 1️⃣ Fetch products dynamically from API
    products_resp = requests.get(CATEGORIES_API_URL, timeout=10)
    if products_resp.status_code != 200:
        print("⚠️ Failed to fetch products")
        return None, 0

    categories = products_resp.json()

    # Flatten products into a dict {product_title_lower: product_info}
    PRODUCTS = {}
    for cat in categories:
        for p in cat.get("products", []):
            PRODUCTS[p["title"].lower()] = p

    delivery_items = []
    total_amount = 0

    for order in orders:
        product_name = order["product_name"].lower()
        color = order.get("color", "").lower() if order.get("color") else ""
        size = order.get("size", "").lower() if order.get("size") else ""
        quantity = float(order.get("quantity", 1))
        total_price = float(order.get("total_price", 0))
        total_amount += total_price

        # Match product ID from PRODUCTS dict
        product_info = PRODUCTS.get(product_name)
        if not product_info:
            print(f"⚠️ Product not found: {product_name}")
            continue

        delivery_items.append({
            "product": product_info["id"],
            "quantity": quantity,
            "size": size,
            "color": color,
            "price": total_price
        })

    if not delivery_items:
        print("⚠️ No valid products to deliver")
        return None, total_amount
    
    # 2️⃣ Map payment mode name → ID
    payment_modes = get_payment_modes()
    selected_payment_mode_name = (customer_details.get("payment_mode") or "").lower()

    payment_mode_id = None
    for pm in payment_modes:
        if pm["mode"].lower() == selected_payment_mode_name:
            payment_mode_id = pm["id"]
            break

    if not payment_mode_id:
        print(f"⚠️ Payment mode not found for: {selected_payment_mode_name}")
        payment_mode_id = None  # fallback

    # Generate bill_no using first order id or timestamp
    bill_no = f"BIL-{orders[0]['id']}-{int(datetime.now().timestamp())}"

    # Prepare payload
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "deliveryDate": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deliver_to": customer_details.get("address"),
        "special_request": "",
        "Current_state": "Ordered",
        "delivery_option": "Express",
        "bill_no": bill_no,
        "order_type": "Instagram",
        "payment_mode": payment_mode_id,  # <-- Add this
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
    api_url = DELIVERY_API_URL
    headers = {"Content-Type": "application/json"}
    response = requests.post(api_url, headers=headers, json=payload)
    return response, total_amount



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
 
        print(f"similarity for {main_title}  {similarity}")
        # matched = fuzzy_category_match(text, all_categories)
        if similarity >= threshold:
            matched.append(cat)

    return matched

# from rapidfuzz import fuzz
# import copy

# def fuzzy_match_products(question: str, all_categories: list, threshold: int = 40):
#     """
#     Fuzzy match the question against product titles.

#     Returns:
#         matched_categories: list of categories containing only matched products
#         matched_products: list of all matched products
#     """
#     question_normalized = question.lower()
#     matched_products = []
#     matched_categories_dict = {}

#     for cat in all_categories:
#         matched_prods_in_cat = []
#         for prod in cat.get("products", []):
#             title = prod.get("title", "").lower()
#             similarity = fuzz.partial_ratio(question_normalized, title)

#             print(f"similarity for prodct {title} {similarity}")
#             if similarity >= threshold:
#                 matched_prods_in_cat.append(prod)
#                 matched_products.append(prod)

#         if matched_prods_in_cat:
#             # Make a copy of the category and replace its products with only matched ones
#             cat_copy = copy.deepcopy(cat)
#             cat_copy["products"] = matched_prods_in_cat
#             matched_categories_dict[cat["id"]] = cat_copy   
#     print("matched_products_", matched_products)
#     # matched_categories = list(matched_categories_dict.values())
#     matched_categories = []
#     return matched_categories, matched_products


from rapidfuzz import fuzz
import copy

def fuzzy_match_products(question: str, all_categories: list, threshold: int = 40):
    """
    Fuzzy match the question against product titles and return sorted matched products by similarity.
    """
    question_normalized = question.lower()
    matched_products = []  # will hold tuples: (similarity, product)
    matched_categories_dict = {}

    for cat in all_categories:
        matched_prods_in_cat = []

        for prod in cat.get("products", []):
            title = prod.get("title", "").lower()
            similarity = fuzz.partial_ratio(question_normalized, title)

            print(f"similarity for product {title}: {similarity}")

            if similarity >= threshold:
                # store similarity with product for sorting later
                prod_with_similarity = copy.deepcopy(prod)
                prod_with_similarity["similarity_score"] = similarity

                matched_prods_in_cat.append(prod_with_similarity)
                matched_products.append(prod_with_similarity)

        if matched_prods_in_cat:
            # Make a copy of the category with only matched & sorted products
            cat_copy = copy.deepcopy(cat)
            cat_copy["products"] = sorted(matched_prods_in_cat, key=lambda x: x["similarity_score"], reverse=True)
            matched_categories_dict[cat["id"]] = cat_copy

    # Sort all matched products globally
    matched_products = sorted(matched_products, key=lambda x: x["similarity_score"], reverse=True)

    # matched_categories = list(matched_categories_dict.values())
    matched_categories = []
    return matched_categories, matched_products


def fetch_all_categories():
    """Safe fetch of categories API; returns list (empty on error)."""
    try:
        resp = requests.get(CATEGORIES_API_URL, timeout=6)
        if resp.status_code == 200:
            return resp.json() or []
    except Exception as e:
        print(f"[catalog] Failed to fetch categories: {e}")
    return []

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
    

    print("text to be compared", text)
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


# def match_products_phrasematcher(text, all_categories):
#     """
#     Matches user text with product titles + descriptions using PhraseMatcher.
#     Returns list of matched products.
#     """

#     matcher = PhraseMatcher(nlp_pm.vocab, attr="LOWER")

#     patterns = []
#     product_map = {}  # map pattern -> product dict

#     for cat in all_categories:
#         for product in cat.get("products", []):
#             title = product.get("title", "").strip()
#             desc = product.get("description", "").strip()

#             if title:
#                 # Title pattern
#                 patterns.append(nlp_pm.make_doc(title))
#                 product_map[title.lower()] = product

#                 # plural/singular handling
#                 if not title.lower().endswith("s"):
#                     plural = title + "s"
#                     patterns.append(nlp_pm.make_doc(plural))
#                     product_map[plural.lower()] = product
#                 else:
#                     singular = title.rstrip("s")
#                     patterns.append(nlp_pm.make_doc(singular))
#                     product_map[singular.lower()] = product

#             # Add description as phrase pattern (optional but useful)
#             if desc:
#                 patterns.append(nlp_pm.make_doc(desc))
#                 product_map[desc.lower()] = product

#     matcher.add("PRODUCTS", patterns)

#     doc = nlp_pm(text)
#     matches = matcher(doc)

#     matched_products = []

#     for match_id, start, end in matches:
#         span_text = doc[start:end].text.lower()
#         product = product_map.get(span_text)

#         if product and product not in matched_products:
#             matched_products.append(product)
#     matched_categories = []
#     return matched_categories, matched_products

# from sentence_transformers import util


from sentence_transformers import util

def match_products_embeddings(text, categories, embedder, top_k=3, min_score=0.3):
    """
    Matches user text with product titles using sentence-transformer embeddings.
    Returns: (matched_categories, matched_products_with_score)
    
    matched_products_with_score = list of dicts:
        { "id": ..., "title": ..., ..., "score": similarity_score }
    """

    # 1️⃣ Flatten product list
    products = []
    product_titles = []
    category_map = {}

    for cat in categories:
        for p in cat.get("products", []):
            title = p.get("title", "").strip()
            if not title:
                continue

            products.append(p)
            product_titles.append(title)
            category_map[title] = cat

    if not products:
        print("No product found")
        return [], []

    # 2️⃣ Create embeddings
    product_embeds = embedder.encode(product_titles, convert_to_tensor=True)

    # 3️⃣ Embed user query
    user_embed = embedder.encode(text, convert_to_tensor=True)

    # 4️⃣ Compute cosine similarity
    cos_scores = util.cos_sim(user_embed, product_embeds)[0]

    # 5️⃣ Get top-k results
    top_results = cos_scores.topk(k=min(top_k, len(products)))

    matched_products = []
    matched_categories = []

    print("\n--- Debug: Similarity Scores ---")
    for score, idx in zip(top_results.values, top_results.indices):
        score_value = float(score)
        matched_product = products[int(idx)]
        print(f"Product: {matched_product['title']} | Score: {score_value:.4f}")

        if score_value >= min_score:
            # Flatten product + score at top level
            matched_product_with_score = matched_product.copy()
            matched_product_with_score['score'] = score_value
            matched_products.append(matched_product_with_score)

            # # Optionally add category
            # cat = category_map.get(matched_product["title"])
            # if cat and cat not in matched_categories:
            #     matched_categories.append(cat)

    return matched_categories, matched_products





def fallback_category_match(user_message: str, all_categories: list):
    """
    Runs PhraseMatcher → Fuzzy Category → Fuzzy Product fallback chain.
    Returns:
        matched_categories (list)
        matched_products (list)
    """

    text = user_message.lower()

    # # 1) PhraseMatcher
    # try:

    #     print("categories for phrasematching", all_categories)
    #     matched_categories = match_categories_phrasematcher(text, all_categories)


    # except Exception:
    #     matched_categories = []
    # print("matched_products_after_phrase category matcher", matched_categories )

    # 1) PhraseMatcher
    try:
        matched_categories, matched_products = match_products_embeddings(text, all_categories, embedder=embedder, top_k=5)
    


    except Exception:
        matched_categories = []
        matched_products = []
    print("matched_categories_after_phrase category matcher", matched_categories )
    print("matched_products_after_phrase category matcher", matched_products )

    # # 2) If PhraseMatcher fails → Fuzzy category match
    # if not matched_categories:
    #     try:
    #         matched_categories = fuzzy_category_match(text, all_categories, threshold=95)
    #     except Exception:
    #         matched_categories = []

    # matched_products = []

    # 3) If still nothing → Fuzzy product match
    # if not matched_categories:
    if not matched_products:
        try:
            matched_categories, matched_products = fuzzy_match_products(text, all_categories, threshold=40)
        except Exception:
            matched_categories, matched_products = [], []

    print("matched_products_after_fuzzy matcher products", matched_products )
    print("matched_categories_after_fuzzy matcher categories", matched_categories )


    return matched_categories, matched_products


import re

def is_number_only(text):
    text = text.strip().lower()
    # number or spelled-out number
    number_words = {"one":1, "two":2, "three":3, "four":4, "five":5, "six":6, "seven":7, "eight":8, "nine":9}
    
    if text.isdigit():
        return int(text)
    if text in number_words:
        return number_words[text]
    return None

import json
from decimal import Decimal
from datetime import datetime

def decimal_default(obj):

    print("for redis obj", obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    # You can add more types here if needed
    return str(obj)  # fallback for unknown types