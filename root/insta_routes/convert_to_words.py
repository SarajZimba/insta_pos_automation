import inflect

def convert_amount_to_words(amount):
    p = inflect.engine()

    # Split the amount into integer and decimal parts
    rupees = int(amount)
    paisa = round((amount - rupees) * 100)

    # Convert the rupees and paisa to words
    rupees_in_words = p.number_to_words(rupees).replace(",", "")
    paisa_in_words = p.number_to_words(paisa).replace(",", "")

    # Format the final string
    if paisa > 0:
        amount_in_words = f"{rupees_in_words} rupees and {paisa_in_words} paisa"
    else:
        amount_in_words = f"{rupees_in_words} rupees"
    print(amount_in_words)
    return amount_in_words

convert_amount_to_words(1015.15)