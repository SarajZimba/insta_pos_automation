import requests

from io import BytesIO
import os

ACCESS_TOKEN = os.getenv('ACCESS_TOKEN')
GRAPH_API_URL = os.getenv('GRAPH_API_URL')
def download_image(url):
    response = requests.get(url)
    if response.status_code == 200:
        return BytesIO(response.content)  # In-memory file
    return None


from PIL import Image
import numpy as np
import json
import numpy as np
from sentence_transformers import SentenceTransformer
import json

from .insta_receive import send_instagram_message

clip_model = SentenceTransformer("clip-ViT-B-32")
def generate_embedding(image_bytes):
    image = Image.open(image_bytes).convert("RGB")
    vector = clip_model.encode(image)  # your CLIP model
    embedding = vector.tolist()         # convert to list for JSON
    return embedding


# def match_product_from_instagram(sender_id, embedding):
#     url = "https://vibezdc.silverlinepos.com/api/products/match-image/"
#     payload = {"embedding": embedding}  # must be list, not string
#     resp = requests.post(url, json=payload, timeout=10)
#     print("status code received", resp.status_code)
#     if resp.status_code == 200:
#         data = resp.json()
#         if data.get("product_name"):
#             message = f"🎯 Matched Product: {data['product_name']}\nSimilarity: {data['similarity']:.2f}"
#             send_instagram_message(sender_id, message)
#         else:
#             send_instagram_message(sender_id, "❌ Could not match the product.")
#     else:
#         send_instagram_message(sender_id, "❌ Failed to check product similarity.")


def match_product_from_instagram(sender_id, embedding):
    url = "https://vibezdc.silverlinepos.com/api/products/match-image/"
    payload = {"embedding": embedding}  # must be list, not string
    resp = requests.post(url, json=payload, timeout=10)

    if resp.status_code != 200:
        send_instagram_message(sender_id, "❌ Failed to check product similarity.")
        return

    data = resp.json()
    print("API response:", data)

    # Handle single product response
    if data.get("product_name"):
        product_title = data["product_name"]

        quick_replies = [{
            "content_type": "text",
            "title": product_title[:20],
            "payload": f"PRODUCT_{product_title.upper().replace(' ', '_')}"
        }]

        payload = {
            "recipient": {"id": sender_id},
            "message": {
                "text": f"🎯 Matched Product: {product_title}\nPrice: {data['price']:.2f}\nPlease select:",
                "quick_replies": quick_replies
            }
        }

        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        requests.post(GRAPH_API_URL, headers=headers, json=payload)

    else:
        send_instagram_message(sender_id, "❌ Could not match the product.")




def handle_instagram_image(sender_id, image_url):
    image_bytes = download_image(image_url)
    if not image_bytes:
        send_instagram_message(sender_id, "❌ Failed to download image.")
        return
    embedding = generate_embedding(image_bytes)
    match_product_from_instagram(sender_id, embedding)
