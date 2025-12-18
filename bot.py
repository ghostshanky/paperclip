#!/usr/bin/env python3
# bot.py - PaperClip (minimal, coding-focused, Gemini + OpenRouter aware)
# Requirements: pip install aiohttp pyperclip
# Run: python bot.py

import asyncio
import aiohttp
import json
import os
import re
import time
import uuid
from pathlib import Path
import pyperclip

BASE = Path(__file__).parent.resolve()
PROVIDERS_FILE = BASE / "providers.json"
SESSIONS_DIR = BASE / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

CLIP_POLL = 0.35
MAX_PROVIDER_RETRIES = 2
REQUEST_TIMEOUT = 90

mode = "off"   # "off" | "all"
current_session = None
last_clip = ""
last_response_time = 0
running = True
preferred_provider_type = "openrouter"

def now_ts(): return int(time.time())

def load_providers():
    if not PROVIDERS_FILE.exists():
        print("providers.json missing.")
        return []
    raw = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
    for p in raw:
        p.setdefault("enabled", True)
        p.setdefault("priority", 100)
        p.setdefault("id", p.get("id") or p.get("name") or str(uuid.uuid4())[:8])
    active = [p for p in raw if p.get("enabled", True)]
    active.sort(key=lambda x: x.get("priority", 100))
    print(f"Loaded {len(active)} provider(s).")
    return active

providers = load_providers()

def start_session(name="local_session"):
    global current_session
    sid = str(uuid.uuid4())[:8]
    current_session = {"id": sid, "name": f"{name}_{sid}", "created_at": now_ts(), "messages": []}
    save_session()
    print(f"Started session {current_session['name']} (id={sid})")
    return current_session

def save_session():
    if not current_session:
        return
    p = SESSIONS_DIR / f"{current_session['id']}.json"
    p.write_text(json.dumps(current_session, indent=2, ensure_ascii=False), encoding="utf-8")

def provider_endpoint(p):
    base = (p.get("base_url") or "").rstrip("/")
    if not base:
        base = "https://openrouter.ai"
    # if user provided full endpoint (contains /v1 or a verb) return as-is
    if "/v1" in base or "/v1beta" in base or base.endswith("/chat/completions"):
        return base
    # default chat completions path for OpenRouter/OpenAI style
    return f"{base}/v1/chat/completions"

def parse_retry_delay_seconds(error_json):
    try:
        details = error_json.get("error", {}).get("details", [])
        for d in details:
            if "RetryInfo" in d.get("@type", ""):
                rd = d.get("retryDelay")
                if rd:
                    s = str(rd).strip()
                    m = re.match(r"^(\d+)(?:\.\d+)?s$", s)
                    if m:
                        return int(float(m.group(1)))
                    m2 = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", s)
                    if m2:
                        hours = int(m2.group(1) or 0)
                        mins = int(m2.group(2) or 0)
                        secs = int(m2.group(3) or 0)
                        return hours*3600 + mins*60 + secs
    except Exception:
        pass
    return 10

_CODE_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.S)
INLINE_CODE_RE = re.compile(r"(^\s*(?:#|//).*$|^\s*[\w\.\-\_]+\s*=|^\s*(?:def|class|for|while|if|return|print)\b.*$)", re.M)
def minimalize_response(text):
    if not text:
        return ""
    blocks = _CODE_FENCE_RE.findall(text)
    if blocks:
        blocks.sort(key=lambda b: len(b), reverse=True)
        code = blocks[0].rstrip()
        if not code.endswith("\n"):
            code += "\n"
        return "// code (extracted)\n" + code
    lines = text.splitlines()
    code_lines = []
    curr = []
    for ln in lines:
        if INLINE_CODE_RE.match(ln.strip()):
            curr.append(ln)
        else:
            if curr:
                code_lines.extend(curr + [""])
                curr = []
    if curr:
        code_lines.extend(curr)
    if sum(1 for l in code_lines if l.strip()) >= 2:
        out = "\n".join(code_lines).strip() + "\n"
        return "// code (heuristic)\n" + out
    compressed = " ".join(text.strip().splitlines())[:1200]
    return "// response (minimal)\n" + compressed

async def call_provider(session, prov, messages, prompt_text):
    """
    Branching provider call:
      - type=='gemini' => Google Generative Language v1beta2 models/{model}:generate
      - else => POST to provider_endpoint(prov) (OpenRouter/OpenAI style chat completions)
    """
    pid = prov.get("id")
    p_type = (prov.get("type") or "").lower()
    # Detective: if prov.base_url already looks like a full endpoint and includes models/...:generate
    # use it verbatim
    try:
        if p_type == "gemini" or "generativelanguage.googleapis.com" in (prov.get("base_url") or ""):
            # Build endpoint: either full base already contains v1 path, or add models/{model}:generate
            base = prov.get("base_url").rstrip("/")
            # prefer canonical: base + /models/{model}:generate
            model = prov.get("model")
            if not model:
                raise Exception("Gemini provider missing 'model' field (e.g. models/gemini-2.0-flash)")
            # if user already wrote full model path (starts with models/) keep it
            model_path = model if model.startswith("models/") else f"models/{model}"
            if base.endswith(":generate") or "models/" in base:
                url = base
            else:
                url = f"{base}/{model_path}:generateContent"
            print(f"[TRY] Provider {pid} (model={model_path}) -> {url}")
            headers = {"x-goog-api-key": prov.get('api_key'), "Content-Type": "application/json"}
            body = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "maxOutputTokens": prov.get("max_tokens", 4096)
                }
            }
            async with session.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
                text = await resp.text()
                if resp.status == 429:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
                    ex = Exception(f"HTTP 429: {json.dumps(data) if data else text}")
                    ex.retry_seconds = parse_retry_delay_seconds(data)
                    raise ex
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {text}")
                data = await resp.json()
                # parse typical Gemini-like response: 'candidates' or 'output'
                # Newer GL responses may be: {"candidates":[{"output":"..."}], "metadata":...}
                content = ""
                # Try common fields:
                if isinstance(data, dict):
                    if "candidates" in data and data["candidates"]:
                        cand = data["candidates"][0]
                        # NEW: Check for nested parts/text structure
                        if "content" in cand and isinstance(cand["content"], dict) and "parts" in cand["content"]:
                            parts = cand["content"]["parts"]
                            if parts and isinstance(parts, list):
                                content = parts[0].get("text", "")
                        else:
                            content = cand.get("content") or cand.get("output") or cand.get("text") or ""
                    elif "output" in data:
                        # output may be string or structured
                        out = data["output"]
                        if isinstance(out, str):
                            content = out
                        elif isinstance(out, list):
                            # join textual pieces
                            parts = []
                            for o in out:
                                if isinstance(o, dict):
                                    parts.append(o.get("content") or o.get("text") or "")
                                elif isinstance(o, str):
                                    parts.append(o)
                            content = "\n".join(p for p in parts if p)
                        else:
                            content = json.dumps(out)
                    elif "candidates" not in data and "result" in data:
                        content = json.dumps(data["result"])
                    else:
                        content = json.dumps(data)
                else:
                    content = str(data)
                return {"ok": True, "text": content, "raw": data, "provider_id": pid}
        else:
            # OpenRouter/OpenAI style chat completions
            url = provider_endpoint(prov)
            print(f"[TRY] Provider {pid} (model={prov.get('model')}) -> {url}")
            headers = {"Authorization": f"Bearer {prov.get('api_key')}", "Content-Type": "application/json"}
            payload = {
                "model": prov.get("model") or "openrouter/auto",
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
                "temperature": 0.0,
                "max_tokens": prov.get("max_tokens", 8192)
            }
            async with session.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
                text = await resp.text()
                if resp.status == 429:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
                    ex = Exception(f"HTTP 429: {json.dumps(data) if data else text}")
                    ex.retry_seconds = parse_retry_delay_seconds(data)
                    raise ex
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}: {text}")
                data = await resp.json()
                if "choices" in data and data["choices"]:
                    ch = data["choices"][0]
                    if "message" in ch and isinstance(ch["message"], dict):
                        content = ch["message"].get("content")
                    else:
                        content = ch.get("text") or json.dumps(ch)
                    return {"ok": True, "text": content, "raw": data, "provider_id": pid}
                return {"ok": True, "text": json.dumps(data), "raw": data, "provider_id": pid}
    except Exception:
        raise

async def send_prompt(prompt_text):
    global providers, current_session
    if not providers:
        raise Exception("No providers configured. Fill providers.json with keys.")
    messages = []
    system_msg = (
        "You are a helpful coding assistant. Provide complete, detailed responses without truncating or summarizing. "
        "When asked for code, return the full code with explanations if needed. Do not limit response length. "
        "The questions which will be asked are mostly hard coding, debugging, programming and object oriented programming questions. "
        "Your solutions must satisfy all test cases, handle edge cases, and use efficient algorithms and clean, readable style."
    )
    messages.append({"role": "system", "content": system_msg})
    if current_session and current_session.get("messages"):
        for m in current_session["messages"][-30:]:
            messages.append({"role": m["role"], "content": m["text"]})
    messages.append({"role": "user", "content": prompt_text})

    last_exc = None
    async with aiohttp.ClientSession() as session:
        provs = [p for p in providers if p.get("enabled", True)]
        if not provs:
            raise Exception("No enabled providers found")
        if preferred_provider_type:
            matching = [p for p in provs if p.get("type") == preferred_provider_type or preferred_provider_type in p.get("id","").lower()]
            others = [p for p in provs if p not in matching]
            provs = matching + others
        for prov in provs:
            disabled_until = prov.get("_disabled_until", 0)
            if disabled_until and time.time() < disabled_until:
                remain = int(disabled_until - time.time())
                print(f"[SKIP] {prov.get('id')} temporarily disabled for {remain}s due to rate limits.")
                continue
            pid = prov.get("id")
            for attempt in range(1, MAX_PROVIDER_RETRIES+1):
                try:
                    res = await call_provider(session, prov, messages, prompt_text)
                    if res and res.get("ok"):
                        raw_text = res.get("text") or ""
                        current_session["messages"].append({"role":"user","text":prompt_text,"ts": now_ts()})
                        current_session["messages"].append({"role":"assistant","text":raw_text,"ts": now_ts(),"provider": pid})
                        save_session()
                        try:
                            pyperclip.copy(raw_text)
                        except Exception:
                            pass
                        print(f"[OK] Response from {pid} copied to clipboard.")
                        return {"provider": pid, "raw": res.get("raw"), "text": raw_text}
                    else:
                        raise Exception("Invalid response structure")
                except Exception as e:
                    retry_secs = getattr(e, "retry_seconds", None)
                    last_exc = e
                    if retry_secs:
                        prov["_disabled_until"] = time.time() + retry_secs
                        print(f"[WARN] Provider {pid} rate-limited. Disabled for {retry_secs}s. Error: {str(e)[:400]}")
                        break
                    print(f"[WARN] Provider {pid} attempt {attempt} failed: {str(e)[:400]}")
                    await asyncio.sleep(0.4 * attempt)
                    continue
        raise Exception(f"All providers failed. Last: {last_exc}")

async def clipboard_watcher():
    global last_clip, mode, running, preferred_provider_type
    print("Clipboard watcher started. Copy text starting with 'agent.prompt' to trigger.")
    while running:
        try:
            txt = pyperclip.paste() or ""
        except Exception:
            txt = ""
        if txt and txt != last_clip:
            last_clip = txt
            t = txt.strip()
            lower = t.lower()
            if lower.startswith("model.gem") or lower == "model.gem":
                preferred_provider_type = "gemini"
                print("[MODE] preferred provider -> gemini")
            elif lower.startswith("model.openr") or lower == "model.openr":
                preferred_provider_type = "openrouter"
                print("[MODE] preferred provider -> openrouter")
            elif lower.startswith("agent.promptall"):
                mode = "all"
                print("[MODE] agent.promptall ON")
            elif lower.startswith("agent.promptone") or lower.startswith("agent.prompt off"):
                mode = "off"
                print("[MODE] agent.prompt OFF")
            elif mode == "all":
                print("[TRIGGER] promptall - sending clipboard")
                asyncio.create_task(process_clip_as_prompt(t))
            elif lower.startswith("agent.prompt"):
                rest = re.sub(r"^agent\.prompt[:\s]*", "", t, flags=re.I)
                if rest.strip():
                    print("[TRIGGER] inline agent.prompt detected.")
                    asyncio.create_task(process_clip_as_prompt(rest))
                else:
                    print("[TRIGGER] 'agent.prompt' token seen - waiting for next clipboard copy for the actual prompt.")
        await asyncio.sleep(CLIP_POLL)

async def process_clip_as_prompt(prompt_text):
    global last_response_time
    current_time = time.time()
    if current_time - last_response_time < 2:
        print("[SKIP] Request too soon after previous one.")
        return
    try:
        if not current_session:
            start_session()
        print("Sending prompt to providers...")
        out = await send_prompt(prompt_text)
        last_response_time = time.time()
        provider = out.get("provider")
        print(f"> Done (provider={provider})")
    except Exception as e:
        print("[ERROR] Prompt handling failed:", str(e))

async def stdin_loop():
    global mode, running, preferred_provider_type
    help_text = """Commands:
  h/help         show this help
  status         show session and mode
  new            start new session
  providers      list loaded providers
  mode all       enable agent.promptall
  mode off       disable agent.promptall
  model gem      prefer Gemini providers
  model openr    prefer OpenRouter providers
  quit / exit    stop bot
"""
    loop = asyncio.get_event_loop()
    while running:
        try:
            cmd = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            cmd = "quit"
        cmd = (cmd or "").strip()
        if not cmd:
            continue
        if cmd in ("h","help"):
            print(help_text)
        elif cmd == "status":
            print("Mode:", mode)
            print("Preferred provider:", preferred_provider_type or "none")
            print("Session:", current_session["name"] if current_session else "none")
            print("Providers:", len([p for p in providers if p.get("enabled", True)]))
        elif cmd == "new":
            start_session()
        elif cmd == "providers":
            for p in providers:
                print(f"- id={p.get('id')} name={p.get('name')} type={p.get('type')} model={p.get('model')} priority={p.get('priority')} endpoint={provider_endpoint(p)}")
        elif cmd.startswith("mode "):
            _,val = cmd.split(None,1)
            if val == "all":
                mode = "all"; print("Mode set to all")
            elif val in ("off","one"):
                mode = "off"; print("Mode set to off")
            else:
                print("mode must be: all or off")
        elif cmd.startswith("model "):
            _,val = cmd.split(None,1)
            if val == "gem":
                preferred_provider_type = "gemini"; print("Preferred provider set to gemini")
            elif val == "openr":
                preferred_provider_type = "openrouter"; print("Preferred provider set to openrouter")
            else:
                print("model must be: gem or openr")
        elif cmd in ("quit","exit"):
            print("Shutting down."); running = False; break
        else:
            print("Unknown command. Type 'help' or 'h'")

async def main():
    global providers
    providers = load_providers()
    start_session("local_session")
    tasks = [asyncio.create_task(clipboard_watcher()), asyncio.create_task(stdin_loop())]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    save_session()
    for t in tasks:
        if not t.done(): t.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted. Saving session.")
        save_session()
