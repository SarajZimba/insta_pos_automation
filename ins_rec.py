
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

                if message.get("message", {}).get("is_echo"):
                    print("🔁 Skipping echo message...")
                    continue

                if message.get("message", {}).get("mid"):
                    message_id = message.get("message", {}).get("mid")


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
                                "https://vibezdc.silverlinepos.com/api/categories/",
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
                            quantity = 1
                            price = float(matched_product["price"])
                            total_price = price * quantity

                            # 4️⃣ Save order to DB
                            save_order_to_db_all(sender_id, matched_product["title"], quantity, total_price)

                            # 5️⃣ Check if product has attributes
                            attr_resp = requests.post(
                                "https://vibezdc.silverlinepos.com/api/products/attributes/name",
                                json={"product_name": matched_product["title"]},
                                timeout=5
                            )
                            attr_data = attr_resp.json() if attr_resp.status_code == 200 else {}
                            attributes = attr_data.get("attributes", {})

                            color_options = attributes.get("color", [])
                            size_options = attributes.get("size", [])

                            # 6️⃣ Ask for attributes if available, else confirm order
                            if color_options or size_options:
                                attr_message = f"⚙️ Please specify the missing details for your product {matched_product['title']}:\n\n"
                                if size_options:
                                    attr_message += "📏 *Available Sizes:*\n" + "\n".join(f"   - {s}" for s in size_options)
                                if color_options:
                                    attr_message += "\n\n🎨 *Available Colors:*\n" + "\n".join(f"   - {c}" for c in color_options)
                                attr_message += (
                                    "\n\n📝 *Reply in this format:*\n"
                                    "👉 `XL Golden, L Blue, XXL Red`\n\n"
                                    "Each item should include size and color if applicable."
                                )
                                send_instagram_message(sender_id, attr_message)
                            else:
                                send_instagram_message(
                                    sender_id,
                                    f"✅ Added {quantity} x {matched_product['title']} (Rs {price} each).\n"
                                    f"Total: Rs {total_price}\n"
                                    "To confirm delivery, reply exactly in this format:\n"
                                    "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                                )

                        except Exception as e:
                            print("⚠️ Failed to process quick reply order:", e)
                            send_instagram_message(sender_id, "❌ Something went wrong while processing your order. Please try again.")


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
                    intent = "small_talk"
                    llama_intent = {"intent": "small_talk", "category_filter": None, "product_name": None, "negative_intent": False}
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
                print("category_filter",category_filter)
                print("negative_intent", negative_intent)
                print("type of category_filter", type(category_filter))
                print("type of product_name", product_name)

                # --- Fetch all categories ---
                resp = requests.get("https://vibezdc.silverlinepos.com/api/categories/", timeout=5)
                all_categories = resp.json() if resp.status_code == 200 else []

                print("api called")

                if intent == "small_talk":
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
                    print("inside place order")
                    order_items = llama_intent.get("order_items", [])

                    print("order items", order_items)
                    if order_items and len(order_items) > 0:
                        try:
                            # 🔹 Fetch all categories and their products
                            category_resp = requests.get(
                                "https://vibezdc.silverlinepos.com/api/categories/",
                                timeout=5
                            )
                            categories = category_resp.json() if category_resp.status_code == 200 else []

                            print("categories fteched")

                            print("len", len(order_items))
                            #   Flatten products into dict and list for embeddings
                            PRODUCTS_LOOKUP = {}
                            PRODUCTS_LIST = []
                            for cat in categories:
                                for p in cat.get("products", []):
                                    PRODUCTS_LIST.append(p["title"])
                                    PRODUCTS_LOOKUP[p["title"]] = p
                            print("products lookup", PRODUCTS_LOOKUP)
                            print("products list",PRODUCTS_LIST)
                            # Create embeddings for all products (you can cache this for performance)
                            PRODUCT_EMBEDS = embedder.encode(PRODUCTS_LIST, convert_to_tensor=True)

                            for item in order_items:
                                product_name_input = item.get("product")
                                print("product name input", product_name_input)
                                quantity = item.get("quantity", 1)

                                if quantity == None:
                                    quantity = 1
                                print("quantity", quantity)
                                if not product_name_input or not quantity:
                                    continue

                                # 🔹 Embed user input
                                user_embed = embedder.encode(product_name_input, convert_to_tensor=True)

                                # 🔹 Find closest product using cosine similarity
                                cos_scores = util.cos_sim(user_embed, PRODUCT_EMBEDS)[0]
                                best_idx = torch.argmax(cos_scores).item()
                                similarity = cos_scores[best_idx].item()
                                matched_product_name = PRODUCTS_LIST[best_idx]

                                print("matched_product name before", matched_product_name)


                                # matched_product_name, similarity = find_closest_product_faiss(product_name_input)
                                print("similarity", similarity)
                                                                
                                if similarity < 0.7:
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

                                print("matched_product name after", matched_product_name)
                                matched_product = PRODUCTS_LOOKUP[matched_product_name]

                                # 🔹 Calculate total price
                                price = float(matched_product["price"])
                                total_price = price * int(quantity)

                                # 🔹 Save order
                                save_order_to_db_all(sender_id, matched_product["title"], quantity, total_price)

                                # 🔹 Fetch pending order to check attributes
                                connection = get_db_connection()
                                cursor = connection.cursor(dictionary=True)
                                cursor.execute("""
                                    SELECT id, product_name FROM orders
                                    WHERE sender_id = %s AND status = 'pending' AND attributes_filled = FALSE
                                    ORDER BY id ASC LIMIT 1
                                """, (sender_id,))
                                pending_order = cursor.fetchone()
                                pending_product_name = pending_order["product_name"] if pending_order else matched_product_name

                                # 🔹 Fetch product attributes if any
                                attr_resp = requests.post(
                                    "https://vibezdc.silverlinepos.com/api/products/attributes/name",
                                    json={"product_name": pending_product_name},
                                    timeout=5
                                )
                                attr_data = attr_resp.json() if attr_resp.status_code == 200 else {}
                                attributes = attr_data.get("attributes", {})

                                color_options = attributes.get("color", [])
                                size_options = attributes.get("size", [])

                                if color_options or size_options:
                                    attr_message = f"⚙️ Please specify the missing details for your product {pending_product_name}:\n\n"
                                    if size_options:
                                        attr_message += "📏 *Available Sizes:*\n" + "\n".join(f"   - {s}" for s in size_options)
                                    if color_options:
                                        attr_message += "\n\n🎨 *Available Colors:*\n" + "\n".join(f"   - {c}" for c in color_options)
                                    attr_message += (
                                        "\n\n📝 *Reply in this format:*\n"
                                        "👉 `XL Golden, L Blue, XXL Red`\n\n"
                                        "Each item should include size and color if applicable."
                                    )
                                    send_instagram_message(sender_id, attr_message)
                                else:
                                    send_instagram_message(
                                        sender_id,
                                        f"✅ Added {quantity} x {matched_product['title']} (Rs {price} each).\n"
                                        f"Total: Rs {total_price}\n"
                                        "To confirm delivery, reply exactly in this format:\n"
                                        "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                                    )

                        except Exception as e:
                            print("⚠️ Order processing failed:", e)
                            send_instagram_message(sender_id, "❌ Something went wrong while processing your order. Please try again.")


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
                                "https://vibezdc.silverlinepos.com/api/allow-negative-sales/",
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
                                    "https://vibezdc.silverlinepos.com/api/products/stockbyname/",
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
                                        "https://vibezdc.silverlinepos.com/api/products/attributes/name",
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
                                        send_instagram_message(
                                            sender_id,
                                            f"✅ Updated attributes for {current_order['product_name']}.\n"
                                            f"Next product: {next_product_name} has no size/color options.\n"
                                            "Please confirm your order using:\n"
                                            "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                                        )
                                        continue
                                except Exception as e:
                                    print("⚠️ Attribute fetch failed:", e)
                                    send_instagram_message(
                                        sender_id,
                                        f"✅ Updated attributes for {current_order['product_name']}.\n"
                                        f"Couldn't fetch attributes for {next_product_name}. Please specify manually."
                                    )
                                    continue
                            else:
                                send_instagram_message(
                                    sender_id,
                                    f"✅ Attributes updated for {current_order['product_name']}.\n"
                                    "🎉 All items are ready! Please confirm your order using:\n"
                                    "CONFIRM | Name: <Your Name> | Address: <Your Address> | Phone: <98xxxxxxxx>"
                                )
                                continue
                        else:
                            send_instagram_message(sender_id, "No pending order found needing attributes.")
                            continue



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


                    # If only products matched → send quick replies
                    if matched_products:
                        # Take top N products (say 5)
                        top_products = matched_products[:5]

                        quick_replies = []
                        for p in top_products:
                            product_title = p['title']
                            quick_replies.append({
                                "content_type": "text",
                                "title": product_title[:20],  # Instagram limit
                                "payload": f"PRODUCT_{product_title.upper().replace(' ', '_')}"
                            })

                        payload = {
                            "recipient": {"id": sender_id},
                            "message": {
                                "text": f"🤔 We found multiple products similar to '{text_lower}'. Please choose one:",
                                "quick_replies": quick_replies
                            }
                        }

                        headers = {
                            "Authorization": f"Bearer {ACCESS_TOKEN}",
                            "Content-Type": "application/json"
                        }
                        requests.post(GRAPH_API_URL, headers=headers, json=payload)


                        return {"status": "ok"}