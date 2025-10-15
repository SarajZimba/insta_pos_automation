from flask import Flask, request, jsonify, Blueprint
import requests
import os
from dotenv import load_dotenv
import mysql.connector
from root.insta_routes.convert_to_words import convert_amount_to_words
from root.utils.ollama_helper import query_ollama

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

def contains_pattern(text, patterns):
    """Check if any regex pattern matches in the text."""
    text = text.lower()
    return any(re.search(pat, text) for pat in patterns)


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
                text = message.get("message", {}).get("text", "").lower()
                # print(text)
                # # Refresh products from the database
                # PRODUCTS = fetch_products()

                # # Show products
                # if "product" in text:
                #     product_list = "\n".join(
                #         [f"{name.capitalize()} - Rs {info['price']}" for name, info in PRODUCTS.items()]
                #     )
                #     print(product_list)
                #     print(sender_id)
                #     send_instagram_message(sender_id, f"Here are our products:\n{product_list}\nReply with 'Order <Product Name>' to place an order.")

                #     # Send product images
                #     for product_name, product_details in PRODUCTS.items():
                #         send_instagram_image(sender_id, product_details["image_url"])

                wants_products = contains_pattern(text, PRODUCT_PATTERNS)
                negative_intent = contains_pattern(text, NEGATIVE_PATTERNS)

                if wants_products and not negative_intent:
                    PRODUCTS = fetch_products_from_api()

                    if not PRODUCTS:
                        send_instagram_message(sender_id, "⚠️ Unable to fetch products right now.")
                    else:
                        product_list = "\n".join([f"{info['title']} - Rs {info['price']}" for info in PRODUCTS.values()])
                        send_instagram_message(sender_id, f"Here are our products:\n{product_list}\nReply with 'Order <Product Name>' to place an order.")
                        
                        for _, product_details in PRODUCTS.items():
                            if product_details.get("image"):
                                send_instagram_image(sender_id, product_details["image"])

                # Place an order
                elif text.startswith("order"):
                    product_name = text.split(" ", 1)[1].strip().lower()
                    if product_name in PRODUCTS:
                        # Insert order with 'pending' status
                        save_order_to_db(sender_id, product_name)
                        send_instagram_message(sender_id, f"You selected {product_name}. Price: Rs {PRODUCTS[product_name]['price']}. Please enter the quantity.")
                    else:
                        send_instagram_message(sender_id, "❌ Invalid product name. Please check and try again.")

                # Handle quantity
                elif text.isdigit():
                    quantity = int(text)
                    order = get_pending_order(sender_id)

                    if order:
                        product_name = order["product_name"]
                        total_price = PRODUCTS[product_name]["price"] * quantity

                        update_order_quantity(order["id"], quantity, total_price)

                        send_instagram_message(sender_id, f"Your order: {quantity} x {product_name} for Rs {total_price}. Type 'confirm' to confirm the order.")
                    else:
                        send_instagram_message(sender_id, "❌ No pending order found. Please place an order first.")

                # Confirm order
                elif text == "confirm":
                    order = get_pending_order(sender_id)

                    if order:
                        product_name = order["product_name"]
                        total_price = order["total_price"]
                        update_order_status(order["id"], "confirmed")

                        post_order_to_ecom(order, sender_id)
                        send_instagram_message(sender_id, f"✅ Order confirmed!\n{order['quantity']} x {product_name} for Rs {total_price}.\nThank you for ordering!")
                    else:
                        send_instagram_message(sender_id, "❌ No pending order found to confirm. Please place an order first.")

                # else:
                    # send_instagram_message(sender_id, "I didn't understand that. Type 'products' to see available products or 'order <Product Name>' to place an order or 'confirm' to confirm the order.")
                else:
                    try:
                        # Optional: provide some system-level context so the model knows its role
                        # context = (
                        #     "You are Saraj's Ai intelligent assistant. "
                        #     "You help users with order status and general inquiries."
                        # )

                        context = (
                            "You are Arcane's AI intelligent assistant. "
                            "Arcane is a skilled backend developer and AI enthusiast with strong expertise in Python, Django, and Flask. "
                            "They frequently work with MySQL databases, REST APIs, and deployment setups using Nginx and Eventlet. "
                            "They also integrate advanced AI/ML features such as YOLO-based computer vision, sales forecasting, and QR code automation. "
                            "Arcane’s projects often involve practical business systems — including canteen billing, loyalty rewards, and inventory management. "
                            "They prefer efficient, accurate, and production-ready code solutions over long theoretical explanations. "
                            "Arcane typically works from 10 AM to 7 PM Nepal time, sometimes extending into the night for debugging or deep technical work. "
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

def get_pending_order(sender_id):
    """Get the pending order for a specific sender"""
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("""
        SELECT * FROM orders WHERE sender_id = %s AND status = 'pending'
    """, (sender_id,))
    order = cursor.fetchone()
    cursor.close()
    connection.close()
    return order

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



