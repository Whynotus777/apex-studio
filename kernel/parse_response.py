#!/usr/bin/env python3
"""
APEX Response Parser — Extracts structured data from agent responses.

Tries JSON first. Falls back to text parsing if the model didn't produce valid JSON.
Returns a normalized JSON object regardless of input format.

Usage:
  python3 kernel/parse_response.py <response_file>
  OR
  echo "$RESPONSE" | python3 kernel/parse_response.py -

Output: JSON to stdout with keys:
  actions_taken, observations, proposed_output, messages[], scratchpad_update, status
"""
import sys
import json
import re


def try_parse_json(text):
    """Try to extract a JSON block from the response."""
    # Try the whole thing as JSON
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find a JSON block inside ```json ... ``` or { ... }
    patterns = [
        r'```json\s*(.*?)\s*```',
        r'```\s*(\{.*?\})\s*```',
        r'(\{[^{}]*"actions_taken"[^{}]*\})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue

    return None


def parse_text_fallback(text):
    """Parse the old text format as fallback."""
    result = {
        "actions_taken": "",
        "observations": "",
        "proposed_output": "",
        "messages": [],
        "scratchpad_update": "",
        "status": "unknown",
        "_parse_method": "text_fallback"
    }

    lines = text.strip().split("\n")
    current_section = None
    section_content = []

    section_map = {
        "actions taken": "actions_taken",
        "actions_taken": "actions_taken",
        "observations": "observations",
        "proposed output": "proposed_output",
        "proposed_output": "proposed_output",
        "output": "proposed_output",
        "messages": "messages_raw",
        "scratchpad update": "scratchpad_update",
        "scratchpad_update": "scratchpad_update",
        "status": "status",
    }

    def flush_section():
        nonlocal current_section, section_content
        if current_section and section_content:
            content = "\n".join(section_content).strip()
            if current_section == "messages_raw":
                result["messages"] = parse_messages_text(content)
            elif current_section == "status":
                result["status"] = content.split("\n")[0].strip()
            else:
                result[current_section] = content
        section_content = []

    for line in lines:
        # Check if this line starts a new section
        matched = False
        for header, key in section_map.items():
            # Match patterns like "ACTIONS TAKEN:", "**ACTIONS TAKEN**:", "1. ACTIONS TAKEN:"
            pattern = rf'^[\d\.\*\s]*\**{re.escape(header)}\**[\s:]*(.*)$'
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                flush_section()
                current_section = key
                remainder = m.group(1).strip()
                if remainder:
                    section_content.append(remainder)
                matched = True
                break

        if not matched and current_section:
            section_content.append(line)

    flush_section()
    return result


def parse_messages_text(text):
    """Parse message lines from text format."""
    messages = []
    valid_agents = {"apex", "scout", "analyst", "builder", "critic"}

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.lower() == "none":
            continue

        # Parse TO:<agent> | TYPE:<type> | CONTENT:<msg>
        to_match = re.search(r'TO:(\S+)', line, re.IGNORECASE)
        type_match = re.search(r'TYPE:(\S+)', line, re.IGNORECASE)
        content_match = re.search(r'CONTENT:(.*)', line, re.IGNORECASE)

        if to_match and content_match:
            to_agent = to_match.group(1).lower().strip()
            msg_type = type_match.group(1).strip() if type_match else "request"
            content = content_match.group(1).strip()

            # Validate target
            if to_agent not in valid_agents:
                messages.append({
                    "to": "apex",
                    "type": "escalation",
                    "content": f"[invalid target: {to_agent}] {content}",
                    "original_target": to_agent
                })
            else:
                messages.append({
                    "to": to_agent,
                    "type": msg_type,
                    "content": content
                })

    return messages


def parse_messages_json(messages_raw):
    """Normalize messages from JSON format."""
    valid_agents = {"apex", "scout", "analyst", "builder", "critic"}
    normalized = []

    if isinstance(messages_raw, str):
        if messages_raw.lower().strip() in ("none", ""):
            return []
        return parse_messages_text(messages_raw)

    if isinstance(messages_raw, list):
        for msg in messages_raw:
            if isinstance(msg, dict):
                to_agent = msg.get("to", "").lower()
                if to_agent not in valid_agents:
                    normalized.append({
                        "to": "apex",
                        "type": "escalation",
                        "content": f"[invalid target: {to_agent}] {msg.get('content', '')}",
                        "original_target": to_agent
                    })
                else:
                    normalized.append({
                        "to": to_agent,
                        "type": msg.get("type", "request"),
                        "content": msg.get("content", "")
                    })
            elif isinstance(msg, str):
                # Single string messages
                for parsed in parse_messages_text(msg):
                    normalized.append(parsed)

    return normalized


def normalize_status(status_raw):
    """Extract clean status from various formats."""
    if not status_raw:
        return {"state": "unknown", "reason": ""}

    status_str = str(status_raw).lower().strip()

    if "needs_review" in status_str:
        stakes_match = re.search(r'needs_review:(\w+)', status_str)
        stakes = stakes_match.group(1) if stakes_match else "low"
        return {"state": "needs_review", "stakes": stakes}
    elif "blocked" in status_str:
        reason_match = re.search(r'blocked:(.+?)(?:\||$)', status_str)
        reason = reason_match.group(1).strip() if reason_match else "unknown"
        return {"state": "blocked", "reason": reason}
    elif "done" in status_str:
        return {"state": "done", "reason": ""}
    else:
        return {"state": "unknown", "reason": status_str}


def parse_response(text):
    """Main entry point: try JSON, fall back to text."""
    # Try JSON first
    parsed = try_parse_json(text)
    if parsed:
        parsed["_parse_method"] = "json"
        # Normalize messages
        if "messages" in parsed:
            parsed["messages"] = parse_messages_json(parsed.get("messages", []))
        # Normalize status
        parsed["status"] = normalize_status(parsed.get("status", ""))
        return parsed

    # Fall back to text parsing
    result = parse_text_fallback(text)
    result["status"] = normalize_status(result.get("status", ""))
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: parse_response.py <file_or_->", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "-":
        text = sys.stdin.read()
    else:
        with open(sys.argv[1]) as f:
            text = f.read()

    result = parse_response(text)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
