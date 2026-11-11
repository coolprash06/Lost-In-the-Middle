"""Groq API calls with retry on rate limits."""
import math
import os
import re
import time
from collections import deque

from dotenv import load_dotenv
from groq import APIStatusError, Groq, RateLimitError

load_dotenv()

MAX_RETRIES = 6
BASE_DELAY = 2  # seconds; doubles each retry: 2, 4, 8, 16, 32, 64

# Reasoning models spend tokens "thinking" before answering. Leave enough room
# so the reasoning doesn't use up the budget and leave the final answer empty.
REASONING_COMPLETION_TOKENS = 1024
# Non-reasoning models only need to output one UUID.
DEFAULT_COMPLETION_TOKENS = 64

# Extra margin added to every token estimate (10%).
SAFETY_MARGIN = 1.10


# Rough characters-per-token for token estimates. Random UUIDs tokenize badly:
# a measured N=200 request (16,388 prompt chars) was counted by Groq as
# 10,506 - 1,024 = 9,482 prompt tokens, i.e. ~1.73 chars/token. The usual
# "4 chars per token" would underestimate by more than 2x and let 413s through.
CHARS_PER_TOKEN = 1.7


def is_reasoning_model(model):
    return model.startswith("openai/gpt-oss")


def is_qwen_model(model):
    return model.startswith("qwen/")


# Qwen models where Groq rejected reasoning_effort="none", so thinking can't be
# turned off and we fall back to reasoning_format="hidden" (filled in at runtime).
# Tested 2026-11-11: qwen/qwen3.8-27b accepts reasoning_effort="none".
_THINKING_CANT_BE_DISABLED = set()


def completion_tokens_for(model):
    """max_completion_tokens sent with each call (also the reply reserve in estimates)."""
    if is_reasoning_model(model) or model in _THINKING_CANT_BE_DISABLED:
        return REASONING_COMPLETION_TOKENS
    return DEFAULT_COMPLETION_TOKENS


def _reasoning_params(model):
    if is_reasoning_model(model):
        return {"reasoning_effort": "low"}
    if is_qwen_model(model):
        if model in _THINKING_CANT_BE_DISABLED:
            return {"reasoning_format": "hidden"}
        return {"reasoning_effort": "none"}  # turn thinking off
    return {}


def strip_think(text):
    """Remove <think>...</think> blocks (and an unclosed <think> to the end)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def estimate_tokens(text):
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_request_tokens(prompt, model):
    """What Groq counts against the TPM limit: input tokens + max_completion_tokens,
    plus a 10% safety margin."""
    return math.ceil((estimate_tokens(prompt) + completion_tokens_for(model)) * SAFETY_MARGIN)


class RequestTooLargeError(Exception):
    """Groq returned 413: one request is bigger than the limit. Retrying never helps."""


class ContextLengthError(Exception):
    """The prompt + reply reserve doesn't fit in the model's context window."""


def _is_context_length_error(e):
    text = f"{e.body} {e.message}".lower()
    return any(s in text for s in ("context_length", "context length", "context window"))


class TokenPacer:
    """Keeps track of tokens sent in the last 60 seconds and waits before a call
    that would go over the tokens-per-minute (TPM) limit."""

    WINDOW = 60  # seconds

    def __init__(self, tpm):
        self.tpm = tpm
        self.sent = deque()  # (timestamp, tokens)

    def _used(self, now):
        while self.sent and now - self.sent[0][0] >= self.WINDOW:
            self.sent.popleft()
        return sum(tokens for _, tokens in self.sent)

    def wait_for(self, tokens, on_countdown=None):
        """Block until `tokens` fits under the TPM limit, then record it.

        on_countdown(seconds_left) is called about once per second while waiting.
        """
        while True:
            now = time.time()
            if self._used(now) + tokens <= self.tpm or not self.sent:
                self.sent.append((now, tokens))
                return
            # Wait until the oldest call drops out of the 60 s window.
            wait = self.sent[0][0] + self.WINDOW - now
            if on_countdown:
                on_countdown(math.ceil(wait))
            time.sleep(min(1, max(wait, 0.05)))


def get_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not found. Copy .env.example to .env and add your key."
        )
    return Groq(api_key=api_key)


def ask(client, model, prompt, on_wait=None):
    """Send one prompt (temperature 0) and return only the final answer text.

    For reasoning models the reasoning comes back in a separate field
    (message.reasoning), which is ignored here; only message.content is returned.
    Returns "" if the model gave no final answer.

    on_wait(seconds, attempt) is called before sleeping on a rate-limit error,
    so the UI can show a friendly message.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        extra = _reasoning_params(model)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_completion_tokens=completion_tokens_for(model),
                **extra,
            )
            # Score only the final answer: drop any <think>...</think> text.
            return strip_think(response.choices[0].message.content or "")
        except APIStatusError as e:
            if (e.status_code == 400 and extra.get("reasoning_effort") == "none"
                    and "reasoning_effort" in f"{e.body} {e.message}"):
                # Groq rejected reasoning_effort="none": thinking can't be turned off.
                # Fall back to hidden reasoning with the larger reply reserve.
                _THINKING_CANT_BE_DISABLED.add(model)
                continue
            if e.status_code == 413:
                # Request too large: will never succeed, so don't retry.
                raise RequestTooLargeError(
                    f"Request too large for model '{model}' (error 413). "
                    "Lower N or check the tokens-per-minute limit. "
                    f"Groq said: {e.message}"
                ) from e
            if _is_context_length_error(e):
                # Prompt too long for this model's context window: don't retry.
                raise ContextLengthError(
                    f"The prompt is too long for model '{model}'s context window. "
                    "Lower N (number of key-value pairs). "
                    f"Groq said: {e.message}"
                ) from e
            if not isinstance(e, RateLimitError):
                raise
            if attempt == MAX_RETRIES:
                raise
            delay = BASE_DELAY * (2 ** (attempt - 1))
            if on_wait:
                on_wait(delay, attempt)
            time.sleep(delay)
