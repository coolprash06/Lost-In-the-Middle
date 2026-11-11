"""Key-value retrieval experiment from 'Lost in the Middle' (Liu et al., 2024)."""
import json
import math
import random
import uuid
from datetime import datetime
from pathlib import Path

from llm import ask, estimate_request_tokens

SECONDS_PER_CALL = 3  # rough Groq latency per call, used for the run-time estimate

# Label -> fraction of the way through the context
POSITIONS = {
    "First": 0.0,
    "25%": 0.25,
    "Middle": 0.5,
    "75%": 0.75,
    "Last": 1.0,
}

RESULTS_DIR = Path(__file__).parent / "results"
EMPTY_RESPONSE = "EMPTY RESPONSE"


def position_index(fraction, n):
    """Convert a fraction (0..1) to a list index in a list of length n."""
    return round(fraction * (n - 1))


def make_kv_data(n, target_index):
    """Return (pairs, target_key, target_value) with the target at target_index."""
    pairs = [(str(uuid.uuid4()), str(uuid.uuid4())) for _ in range(n)]
    target_key, target_value = pairs[target_index]
    return pairs, target_key, target_value


HEX = "0123456789abcdef"


def make_lookalike_key(key):
    """Copy key and change 2-3 random hex characters (hyphens and length kept).
    Every changed character gets a different digit, so it never equals key."""
    chars = list(key)
    hex_positions = [i for i, c in enumerate(chars) if c != "-"]
    for i in random.sample(hex_positions, random.choice([2, 3])):
        chars[i] = random.choice(HEX.replace(chars[i], ""))
    return "".join(chars)


def max_lookalikes(n):
    """Look-alikes can't go at the target or right next to it: N - 3 slots at most."""
    return n - 3


def make_hard_kv_data(n, target_index, num_lookalikes):
    """Like make_kv_data, but `num_lookalikes` of the distractors have keys that
    look like the target key. They are spread randomly over the list, never at
    or right next to the target. Returns (pairs, key, value, lookalike_values)."""
    pairs, target_key, target_value = make_kv_data(n, target_index)

    lookalike_keys = set()
    while len(lookalike_keys) < num_lookalikes:
        lookalike_keys.add(make_lookalike_key(target_key))

    allowed = [i for i in range(n) if abs(i - target_index) > 1]
    slots = random.sample(allowed, num_lookalikes)
    lookalike_values = set()
    for slot, la_key in zip(slots, lookalike_keys):
        la_value = str(uuid.uuid4())
        pairs[slot] = (la_key, la_value)
        lookalike_values.add(la_value)
    return pairs, target_key, target_value, lookalike_values


def build_prompt(pairs, target_key):
    # Python dicts keep insertion order, so the target stays at its position.
    kv_json = json.dumps(dict(pairs), indent=1)
    return (
        "Extract the value corresponding to the specified key in the JSON object below.\n\n"
        f"JSON data:\n{kv_json}\n\n"
        f"What is the value for key \"{target_key}\"? Answer with only the value."
    )


def tokens_per_request(n, model):
    """Estimated tokens of one request with n pairs (UUIDs are fixed-length,
    so this is the same for every trial)."""
    pairs, key, _ = make_kv_data(n, 0)
    return estimate_request_tokens(build_prompt(pairs, key), model)


def max_n_for_tpm(tpm, model, upper=2000):
    """Largest N whose single request fits under the TPM limit (0 if none)."""
    lo, hi = 0, upper
    while lo < hi:  # binary search; tokens grow with n
        mid = (lo + hi + 1) // 2
        if tokens_per_request(mid, model) <= tpm:
            lo = mid
        else:
            hi = mid - 1
    return lo


def estimate_run_seconds(calls, tokens_per_call, tpm):
    """Rough total run time: how many 60 s windows the pacing needs, plus latency."""
    per_minute = max(1, tpm // tokens_per_call)
    windows = math.ceil(calls / per_minute) - 1
    return windows * 60 + calls * SECONDS_PER_CALL


def run_experiment(client, model, n, trials, on_progress=None, on_wait=None,
                   pacer=None, on_countdown=None, lookalikes=0):
    """Run T trials at each of the 5 positions. Returns a list of result dicts.

    If a TokenPacer is given, each call waits until it fits under the TPM limit.
    If lookalikes > 0 (hard mode), that many distractor keys look like the target key.
    """
    results = []
    total = len(POSITIONS) * trials
    done = 0

    for label, fraction in POSITIONS.items():
        idx = position_index(fraction, n)
        correct = 0
        empty = 0
        fooled = 0
        answers = []
        for _ in range(trials):
            if lookalikes:
                pairs, key, value, lookalike_values = make_hard_kv_data(n, idx, lookalikes)
            else:
                pairs, key, value = make_kv_data(n, idx)
                lookalike_values = set()
            prompt = build_prompt(pairs, key)
            if pacer:
                pacer.wait_for(estimate_request_tokens(prompt, model), on_countdown)
            # Only the final answer is scored (never the reasoning text).
            answer = ask(client, model, prompt, on_wait=on_wait)
            if not answer:
                empty += 1  # an empty answer counts as wrong
                answer, is_correct = EMPTY_RESPONSE, False
            else:
                is_correct = value in answer
                correct += is_correct
            # Wrong answer that is the value of a look-alike key?
            is_fooled = not is_correct and any(v in answer for v in lookalike_values)
            fooled += is_fooled
            answers.append({
                "target_key": key,
                "expected": value,
                "answer": answer,
                "correct": is_correct,
                "fooled_by_lookalike": is_fooled,
            })
            done += 1
            if on_progress:
                on_progress(done, total, label)

        results.append({
            "position": label,
            "index": idx,
            "correct": correct,
            "trials": trials,
            "accuracy": 100 * correct / trials,
            "empty_responses": empty,
            "fooled_by_lookalike": fooled,
            "answers": answers,
        })
    return results


def save_results(model, n, trials, results, hard_mode=False, lookalikes=0):
    """Save a run to results/<timestamp>.json and return the file path."""
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_model = model.replace("/", "_")
    suffix = "_hard" if hard_mode else ""
    path = RESULTS_DIR / f"{timestamp}_{safe_model}_N{n}_T{trials}{suffix}.json"
    data = {
        "model": model,
        "N": n,
        "T": trials,
        "hard_mode": hard_mode,
        "lookalikes": lookalikes if hard_mode else 0,
        "timestamp": timestamp,
        "results": results,
    }
    path.write_text(json.dumps(data, indent=2))
    return path
