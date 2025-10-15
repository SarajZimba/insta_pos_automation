import subprocess

OLLAMA_PATH = "/usr/local/bin/ollama"

def clean_output(output: str) -> str:
    """Basic cleanup for Ollama output."""
    return output.strip()

def query_ollama(context, question, model="llama3.2:3b"):
    """
    Ask Ollama model with optional context.
    - Uses context primarily, but if not sufficient, relies on its own knowledge.
    """

    if context.strip():
        prompt = (
            f"You are Arcane's intelligent assistant. Use the provided context below as your primary source. "
            f"If the answer cannot be fully found in the context, you may use your own general knowledge to provide a helpful, accurate response.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {question}\n\n"
            f"Answer clearly and concisely:"
        )
    else:
        prompt = (
            f"You are Arcane's intelligent assistant. Be helpful, clear, and concise.\n\n"
            f"Question: {question}\n\n"
            f"Answer:"
        )

    result = subprocess.run(
        [OLLAMA_PATH, "run", model],
        input=prompt.encode("utf-8"),
        capture_output=True
    )
    raw_output = result.stdout.decode("utf-8")
    output = clean_output(raw_output)

    return output