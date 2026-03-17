#!/usr/bin/env python3
"""
APEX Model Caller — Handles Ollama and Claude API calls cleanly.
Usage: python3 kernel/call_model.py <model> <system_prompt_file> <user_prompt_file>

Reads prompts from files to avoid shell escaping nightmares.
"""
import sys
import os
import json
import urllib.request
import urllib.error

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

def call_ollama(model, system_prompt, user_prompt, temperature=0.3):
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "think": False,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 4096
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")
    except Exception as e:
        print(f"Ollama error: {e}", file=sys.stderr)
        return ""


def call_claude(model, system_prompt, user_prompt, temperature=0.3):
    if not ANTHROPIC_API_KEY:
        print("No ANTHROPIC_API_KEY, falling back to Ollama", file=sys.stderr)
        return call_ollama("qwen3.5-apex", system_prompt, user_prompt, temperature)

    payload = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": temperature
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["content"][0]["text"]
    except Exception as e:
        print(f"Claude error: {e}", file=sys.stderr)
        return ""


def call_gemini(model, system_prompt, user_prompt, temperature=0.3):
    if not GOOGLE_API_KEY:
        print("No GOOGLE_API_KEY set for Gemini", file=sys.stderr)
        return ""

    payload = json.dumps({
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [{
            "role": "user",
            "parts": [{"text": user_prompt}]
        }],
        "generationConfig": {
            "temperature": temperature,
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GOOGLE_API_KEY}"
        ),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            texts = [part.get("text", "") for part in parts if isinstance(part, dict) and part.get("text")]
            return "\n".join(texts).strip()
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = str(e)
        print(f"Gemini HTTP error: {e.code} {error_body}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"Gemini error: {e}", file=sys.stderr)
        return ""


def main():
    if len(sys.argv) < 4:
        print("Usage: call_model.py <model> <system_prompt_file> <user_prompt_file> [temperature]", file=sys.stderr)
        sys.exit(1)

    model = sys.argv[1]
    system_prompt = open(sys.argv[2]).read()
    user_prompt = open(sys.argv[3]).read()
    temperature = float(sys.argv[4]) if len(sys.argv) > 4 else 0.3

    # Route based on model name
    if model.startswith("claude-opus"):
        result = call_claude("claude-opus-4-20250514", system_prompt, user_prompt, temperature)
    elif model.startswith("claude-sonnet"):
        result = call_claude("claude-sonnet-4-20250514", system_prompt, user_prompt, temperature)
    elif model.startswith("gemini"):
        result = call_gemini(model, system_prompt, user_prompt, temperature)
    else:
        result = call_ollama(model, system_prompt, user_prompt, temperature)

    if result:
        print(result)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
