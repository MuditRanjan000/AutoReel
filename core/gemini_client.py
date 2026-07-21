"""
core/gemini_client.py
Unified AI client: tries Groq first (14,400 free req/day), falls back to
Gemini on error. All agents call generate_with_rotation() — they don't
need to know which provider is serving them.

Priority:
  1. Groq (llama-3.3-70b-versatile) — primary, 100,000 tokens/day free per key
  2. Gemini (gemini-flash-latest)    — fallback, key rotation on 429
"""

import os
from config.settings import GROQ_API_KEYS, GROQ_MODEL, GEMINI_API_KEYS, GEMINI_MODEL, NVIDIA_API_KEY


# ── Groq ──────────────────────────────────────────────────────────────────────

def _is_groq_quota_error(e: Exception) -> bool:
    err = str(e)
    print(f"RAW GROQ EXCEPTION: {err}")
    return "429" in err or "rate_limit" in err.lower() or "limit reached" in err.lower() or "tokens per day" in err.lower() or "organization_restricted" in err.lower()


_groq_key_idx = 0

def _call_groq(prompt: str, response_format: dict = None, temperature: float = 0.85) -> str:
    """Try each Groq key in sequence using round-robin. Rotate on 429. If all keys exhaust, wait and retry globally."""
    import time
    import requests
    global _groq_key_idx
    
    models_to_try = [GROQ_MODEL]
    MAX_GLOBAL_RETRIES = 3
    num_keys = len(GROQ_API_KEYS)
    
    for model in models_to_try:
        for _global_attempt in range(MAX_GLOBAL_RETRIES):
            last_error = None
            all_fatal_errors = True
            
            for offset in range(num_keys):
                # Calculate the actual index using round-robin
                current_idx = (_groq_key_idx + offset) % num_keys
                api_key = GROQ_API_KEYS[current_idx]
                label = f"key {current_idx+1}/{num_keys} ({model})"
                
                try:
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": 1024
                    }
                    if response_format:
                        payload["response_format"] = response_format
                        
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                    
                    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            requests.post,
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=25.0
                        )
                        try:
                            response = future.result(timeout=60.0)
                        except FuturesTimeoutError:
                            print(f"[GeminiClient] {label} HARD TIMEOUT (60s), retrying...", flush=True)
                            continue
                    
                    if response.status_code != 200:
                        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
                        
                    data = response.json()
                    
                    if _global_attempt > 0 or offset > 0:
                        print(f"[Groq] Success on {label} (attempt {_global_attempt+1})", flush=True)
                    
                    # On success, advance the global index for the next completely new function call
                    _groq_key_idx = (current_idx + 1) % num_keys
                    return data["choices"][0]["message"]["content"]
                    
                except Exception as e:
                    err = str(e)
                    if "401" in err or "invalid_api_key" in err.lower():
                        print(f"[Groq] {label} INVALID KEY — skipping...", flush=True)
                        last_error = e
                        continue
                    if _is_groq_quota_error(e):
                        print(f"[Groq] {label} quota exhausted — skipping to next key...", flush=True)
                        last_error = e
                        continue
                        
                    all_fatal_errors = False
                    raise

            # If we reach here, all keys failed this round
            if all_fatal_errors and last_error:
                print(f"[Groq] All keys hit fatal limits (Quota/401). Breaking global retry to failover instantly.", flush=True)
                break

            if _global_attempt < MAX_GLOBAL_RETRIES - 1:
                wait_secs = 30 * (2 ** _global_attempt)
                print(f"[Groq] All keys hit rate limits. Waiting {wait_secs}s before global retry {_global_attempt+2}/{MAX_GLOBAL_RETRIES}...", flush=True)
                time.sleep(wait_secs)

    raise RuntimeError(
        f"All Groq keys exhausted."
    ) from last_error


# ── Gemini Fallback ────────────────────────────────────────────────────────────

def _is_quota_error(e: Exception) -> bool:
    err = str(e)
    return "429" in err or "503" in err or "UNAVAILABLE" in err or "quota" in err.lower() or "RESOURCE_EXHAUSTED" in err



# Model cascade — ordered from BEST/SMALLEST quota to HIGHEST quota.
# The pipeline uses the configured GEMINI_MODEL as primary; others are automatic fallbacks.
# If a model hits a sustained 429 (doesn't recover after 2 mins) or 404, we cascade down.
_GEMINI_MODEL_CASCADE = [
    "gemini-2.5-flash-lite",   # Fast fallback
    "gemini-flash-latest",     # Stable high-volume fallback
    "gemini-2.5-flash",        # Standard multimodal fallback
    "gemini-3.5-flash",        # New architecture fallback
]

def _call_gemini(prompt: str, response_format: dict = None, temperature: float = 0.85) -> str:
    """Try each Gemini key × model in a cascade with global retry on 503 overload.

    Error handling strategy:
      - 429 / RESOURCE_EXHAUSTED  → daily quota; skip this key (5s sleep)
      - 503 / UNAVAILABLE         → server overload; skip key (15s sleep);
                                    if ALL keys get 503, wait 30s/60s and retry
      - anything else             → non-retryable; raise immediately

    Model cascade: if all keys for a model return 429 (daily limit hit),
    automatically falls through to the next model in _GEMINI_MODEL_CASCADE.
    """
    from google import genai
    import time

    MAX_GLOBAL_RETRIES = 3

    # Build ordered model list: configured model first, then cascade fallbacks
    models_to_try = [GEMINI_MODEL] + [m for m in _GEMINI_MODEL_CASCADE if m != GEMINI_MODEL]

    for model in models_to_try:
        for _global_attempt in range(MAX_GLOBAL_RETRIES):
            last_error = None
            all_daily_quota = True  # Track if all failures were daily-quota 429s
            all_fatal_errors = False

            for i, api_key in enumerate(GEMINI_API_KEYS):
                label = f"{model} key {i+1}/{len(GEMINI_API_KEYS)}"
                try:
                    client   = genai.Client(api_key=api_key, http_options={'timeout': 300000})
                    
                    # Convert response_format dict to Gemini config if JSON is requested
                    config = None
                    if response_format and response_format.get("type") == "json_object":
                        config = genai.types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=temperature
                        )
                    else:
                        config = genai.types.GenerateContentConfig(
                            temperature=temperature
                        )
                    
                    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            client.models.generate_content,
                            model=model,
                            contents=prompt,
                            config=config
                        )
                        try:
                            response = future.result(timeout=300.0)
                        except FuturesTimeoutError:
                            print(f"[Gemini] {label} HARD TIMEOUT (300s), retrying...", flush=True)
                            continue
                            
                    if model != GEMINI_MODEL or i > 0 or _global_attempt > 0:
                        print(f"[Gemini] Success on {label} (attempt {_global_attempt+1})", flush=True)
                    return response.text
                except Exception as e:
                    err_str   = str(e).lower()
                    err_type  = type(e).__name__.lower()
                    is_503    = ("503" in err_str or "unavailable" in err_str or "disconnected" in err_str or 
                                 "timeout" in err_str or "connection" in err_str or "protocol" in err_str or
                                 "timeout" in err_type or "connect" in err_type or "disconnect" in err_type or 
                                 "protocol" in err_type or "unavailable" in err_type or "httpcore" in err_type or "httpx" in err_type)
                    is_quota  = "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str or "quota" in err_type or "resource_exhausted" in err_type
                    is_404    = "404" in err_str or "not found" in err_str or "notfound" in err_type
                    
                    if is_404:
                        print(f"[Gemini] {model} returned 404 Not Found. Skipping model...", flush=True)
                        last_error = e
                        # Break out of the keys loop and set a flag to break global retry loop
                        break

                    if is_503 or is_quota:
                        if "timeout" in err_str.lower():
                            print(f"[Gemini] {label} hit NETWORK TIMEOUT. Breaking all retries to failover instantly.", flush=True)
                            last_error = e
                            all_fatal_errors = True
                            break
                        sleep_secs = 3 if is_503 else 5
                        print(f"[Gemini] {label} hit {'transient/network error' if is_503 else 'quota limit'} - sleeping {sleep_secs}s...", flush=True)
                        time.sleep(sleep_secs)
                        last_error = e
                        continue
                    raise  # non-retryable
            
            # If we broke out due to 404, we want to break the global retry loop to try the next model
            if last_error and ("404" in str(last_error) or "not found" in str(last_error).lower()):
                break

            if not GEMINI_API_KEYS:
                last_error = ValueError("No valid Gemini API keys configured in the environment.")
                print(f"[Gemini] Sustained error for {model} (NoKeysConfigured). Cascading to next model...", flush=True)
                break # Break global retry loop, try next model in cascade

            if all_fatal_errors and last_error:
                print(f"[Gemini] Fatal network issue detected. Skipping all Gemini retries.", flush=True)
                break
                
            # If we get here and last_error is not None, all keys failed this global attempt.
            if _global_attempt < MAX_GLOBAL_RETRIES - 1:
                wait_secs = 5 * (2 ** _global_attempt)   # 5s → 10s
                print(f"[Gemini] All keys hit errors on {model}. "
                      f"Waiting {wait_secs}s before global retry "
                      f"{_global_attempt+2}/{MAX_GLOBAL_RETRIES}...", flush=True)
                time.sleep(wait_secs)
            else:
                # All global attempts exhausted for this model.
                print(f"[Gemini] Sustained error for {model} ({type(last_error).__name__}). Cascading to next model...", flush=True)
                break # Break global retry loop, try next model in cascade

    raise RuntimeError(
        f"All Gemini models and keys exhausted across cascade: {models_to_try}"
    ) from last_error



# ── Public API ─────────────────────────────────────────────────────────────────

def _call_nvidia(prompt: str, response_format: dict = None, temperature: float = 0.85) -> str:
    import requests
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json"
    }
    strict_prompt = prompt
    if response_format and response_format.get("type") == "json_object":
        strict_prompt = "CRITICAL INSTRUCTION: You MUST output ONLY a valid JSON object. Do not output any conversational text, markdown formatting, or preamble.\\n\\n" + prompt

    payload = {
        "model": "meta/llama-3.3-70b-instruct",
        "messages": [{"role": "user", "content": strict_prompt}],
        "temperature": temperature,
        "max_tokens": 1024
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"].get("content")
    if not content:
        raise ValueError(f"NVIDIA returned empty or None content: {resp.text}")
    return content

_openrouter_key_idx = 0

# Ordered list of OpenRouter free models to try if the primary one goes down or hits severe rate limits.
_OPENROUTER_MODEL_CASCADE = [
    "google/gemma-4-26b-a4b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen-2.5-72b-instruct:free"
]

def _call_openrouter(prompt: str, response_format: dict = None, temperature: float = 0.85) -> str:
    import requests
    import time
    from config.settings import OPENROUTER_API_KEYS, OPENROUTER_MODEL
    global _openrouter_key_idx
    
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    MAX_GLOBAL_RETRIES = 2
    num_keys = len(OPENROUTER_API_KEYS)
    
    # Build ordered model list: configured model first, then cascade fallbacks
    models_to_try = [OPENROUTER_MODEL] + [m for m in _OPENROUTER_MODEL_CASCADE if m != OPENROUTER_MODEL]
    
    i = 0
    dynamic_fetched = False
    
    while i < len(models_to_try):
        model = models_to_try[i]
        for _global_attempt in range(MAX_GLOBAL_RETRIES):
            last_error = None
            all_fatal_errors = False
        for offset in range(num_keys):
            current_idx = (_openrouter_key_idx + offset) % num_keys
            api_key = OPENROUTER_API_KEYS[current_idx]
            label = f"OpenRouter key {current_idx+1}/{num_keys}"
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            strict_prompt = prompt
            if response_format and response_format.get("type") == "json_object":
                strict_prompt = "CRITICAL INSTRUCTION: You MUST output ONLY a valid JSON object. Do not output any conversational text, markdown formatting, or preamble.\\n\\n" + prompt

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": strict_prompt}],
                "temperature": temperature,
                "max_tokens": 1024
            }
                
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                try:
                    content = data["choices"][0]["message"].get("content")
                    if not content:
                         raise ValueError(f"OpenRouter returned empty or None content: {data}")
                    if _global_attempt > 0 or offset > 0:
                        print(f"[OpenRouter] Success on {label} (attempt {_global_attempt+1})")
                        
                    # On success, advance the global index for the next completely new function call
                    _openrouter_key_idx = (current_idx + 1) % num_keys
                    return content
                except KeyError:
                    print(f"[OpenRouter] KeyError 'choices'. Raw response: {data}", flush=True)
                    raise ValueError(f"OpenRouter returned invalid JSON format: {data}")
            except Exception as e:
                err_str = str(e)
                if "401" in err_str:
                    print(f"[OpenRouter] {label} INVALID KEY — skipping...")
                    last_error = e
                    continue
                if "404" in err_str:
                    print(f"[OpenRouter] {model} returned 404 (unavailable for free). Cascading to next model...")
                    last_error = e
                    all_fatal_errors = True
                    break
                if "429" in err_str:
                    print(f"[OpenRouter] {label} quota exhausted — sleeping 5s and trying next...")
                    time.sleep(5)
                    last_error = e
                    continue
                raise # non-retryable
        
        # If model is 404 or all keys hit network errors, break the retry loop and cascade to next model
        if all_fatal_errors and last_error:
            break
            
        # If we reach here, all keys failed this round (usually 429)
        if _global_attempt < MAX_GLOBAL_RETRIES - 1:
            wait_secs = 15 * (2 ** _global_attempt)
            print(f"[OpenRouter] All keys hit rate limits for {model}. Waiting {wait_secs}s before global retry {_global_attempt+2}/{MAX_GLOBAL_RETRIES}...")
            time.sleep(wait_secs)
        else:
            print(f"[OpenRouter] Sustained rate limits for {model}. Cascading to next model...")
            
            if i == len(models_to_try) - 1 and not dynamic_fetched:
                print("[OpenRouter] Curated model cascade exhausted. Fetching ALL live free models dynamically as last resort...")
                try:
                    resp = requests.get("https://openrouter.ai/api/v1/models", timeout=15)
                    if resp.status_code == 200:
                        for m in resp.json().get('data', []):
                            if m.get('pricing', {}).get('prompt') == '0' and m.get('pricing', {}).get('completion') == '0':
                                if m['id'] not in models_to_try:
                                    models_to_try.append(m['id'])
                except Exception as e:
                    print(f"[OpenRouter] Failed to fetch dynamic models: {e}")
                dynamic_fetched = True
                
            break # Exhausted retries for this model, cascade to next
            
        i += 1

    raise RuntimeError(
        f"All OpenRouter keys and models exhausted across cascade: {models_to_try}"
    ) from last_error

def generate_with_rotation(prompt: str, use_gemini_fallback: bool = False, response_format: dict = None, temperature: float = 0.85) -> str:
    """
    Primary entry point for all agents.
    Tries Groq first with key rotation.
    If Groq fails entirely, it automatically falls back to Nvidia, then Gemini to ensure the pipeline never crashes.
    """

    # 1. Try Groq with key rotation + extended retry
    if GROQ_API_KEYS:
        try:
            result = _call_groq(prompt, response_format=response_format, temperature=temperature)
            if not result:
                raise ValueError("Groq returned empty or None content")
            return result
        except Exception as e:
            print(f"[AIClient] Groq completely failed or restricted: {e}", flush=True)

    from config.settings import OPENROUTER_API_KEYS, NVIDIA_API_KEY

    # 2. OpenRouter fallback
    if OPENROUTER_API_KEYS:
        try:
            print("[AIClient] Groq exhausted. AUTO-FALLING BACK TO OPENROUTER...", flush=True)
            result = _call_openrouter(prompt, response_format=response_format, temperature=temperature)
            if not result:
                raise ValueError("OpenRouter returned empty or None content")
            return result
        except Exception as e:
            print(f"[AIClient] OpenRouter failed: {e}", flush=True)
            
    # 2.5 Nvidia fallback
    if NVIDIA_API_KEY:
        try:
            print("[AIClient] OpenRouter exhausted. AUTO-FALLING BACK TO NVIDIA...", flush=True)
            result = _call_nvidia(prompt, response_format=response_format, temperature=temperature)
            if not result:
                raise ValueError("Nvidia returned empty or None content")
            return result
        except Exception as e:
            print(f"[AIClient] Nvidia failed: {e}", flush=True)

    # 3. Gemini ultimate fallback
    print("[AIClient] Groq completely exhausted. AUTO-FALLING BACK TO GEMINI to prevent pipeline crash...", flush=True)
    result = _call_gemini(prompt, response_format=response_format, temperature=temperature)
    if not result:
        raise ValueError("Gemini returned empty or None content (possibly due to safety filters)")
    return result



def generate_with_gemini(prompt: str) -> str:
    """
    Direct Gemini call — used ONLY by the AI video reviewer.
    Bypasses Groq entirely. Preserves Gemini quota for visual review tasks.
    """
    return _call_gemini(prompt)
