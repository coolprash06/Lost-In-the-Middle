"""Streamlit UI for the 'Lost in the Middle' demo.

Note: this app deliberately avoids pandas/pyarrow (blocked by Windows Smart App
Control on some machines). Use plain lists/dicts, plotly.graph_objects, and
no Arrow-based Streamlit elements (st.dataframe, st.table, st.line_chart, ...).
"""
import plotly.graph_objects as go
import streamlit as st

from experiments import (
    POSITIONS, estimate_run_seconds, max_lookalikes, max_n_for_tpm, run_experiment,
    save_results, tokens_per_request,
)
from llm import ContextLengthError, RequestTooLargeError, TokenPacer, get_client

st.set_page_config(page_title="Lost in the Middle", layout="wide")
st.title("Lost in the Middle")
st.caption(
    "Based on Liu et al., *Lost in the Middle: How Language Models Use Long Contexts* (TACL 2024)."
)

# ---------- Sidebar ----------
st.sidebar.header("Settings")
model = st.sidebar.text_input("Model name", value="openai/gpt-oss-20b")
n_pairs = st.sidebar.slider("Number of key-value pairs (N)", 20, 500, 75)
trials = st.sidebar.slider("Trials per position (T)", 1, 10, 3)
tpm_limit = st.sidebar.number_input(
    "Tokens-per-minute limit", min_value=1000, value=8000, step=1000,
    help="Groq free tier for openai/gpt-oss-20b is 8000 TPM.",
)
hard_mode = st.sidebar.checkbox("Hard mode (look-alike keys)", value=False)
lookalikes = 0
if hard_mode:
    lookalikes = st.sidebar.slider(
        "Number of look-alike keys", 1, min(30, max_lookalikes(n_pairs)), 10,
        help="Distractor keys that differ from the target key in 2-3 hex characters.",
    )
st.sidebar.markdown("Temperature: **0** (fixed)")

# ---------- U-Curve Tester ----------
st.subheader("U-Curve Tester")
st.write(
    f"Builds a JSON object of **{n_pairs}** random UUID key-value pairs, hides one target "
    "pair at a chosen position, and asks the model for its value. "
    "Tested positions: " + ", ".join(POSITIONS) + "."
)
n_calls = len(POSITIONS) * trials
req_tokens = tokens_per_request(n_pairs, model)
too_large = req_tokens > tpm_limit

if too_large:
    max_n = max_n_for_tpm(tpm_limit, model)
    st.error(
        f"One request at N={n_pairs} is about **{req_tokens:,} tokens**, which is over the "
        f"tokens-per-minute limit of {tpm_limit:,}. Groq would reject it (error 413). "
        + (f"The maximum N that fits is **{max_n}**." if max_n > 0
           else "No N fits under this limit.")
    )
else:
    secs = estimate_run_seconds(n_calls, req_tokens, tpm_limit)
    st.info(
        f"Estimated API calls: **{n_calls}** (5 positions x {trials} trials) | "
        f"Estimated run time: **~{secs // 60} min {secs % 60} s** "
        f"(~{req_tokens:,} tokens per request, limit {tpm_limit:,}/min)"
    )

if st.button("Run experiment", type="primary", disabled=too_large):
    try:
        client = get_client()
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    progress = st.progress(0.0, text="Starting...")
    status = st.empty()

    def on_progress(done, total, label):
        progress.progress(done / total, text=f"Call {done}/{total} - position: {label}")
        status.empty()

    def on_wait(seconds, attempt):
        status.warning(
            f"Rate limit hit - waiting {seconds}s before retrying (attempt {attempt}). "
            "This is normal on the Groq free tier."
        )

    def on_countdown(seconds):
        status.info(f"Waiting {seconds}s for rate limit...")

    # Keep one pacer across runs so back-to-back runs also respect the limit.
    pacer = st.session_state.setdefault("pacer", TokenPacer(tpm_limit))
    pacer.tpm = tpm_limit

    try:
        results = run_experiment(
            client, model, n_pairs, trials, on_progress, on_wait,
            pacer=pacer, on_countdown=on_countdown, lookalikes=lookalikes,
        )
    except (RequestTooLargeError, ContextLengthError) as e:
        st.error(str(e))
        st.stop()
    except Exception as e:
        st.error(f"Experiment failed: {e}")
        st.stop()

    progress.progress(1.0, text="Done!")
    path = save_results(model, n_pairs, trials, results, hard_mode, lookalikes)
    st.session_state["last_run"] = {
        "model": model, "N": n_pairs, "T": trials, "hard_mode": hard_mode,
        "results": results, "path": str(path),
    }

run = st.session_state.get("last_run")
if run:
    results = run["results"]
    positions = [r["position"] for r in results]
    accuracies = [r["accuracy"] for r in results]
    run_hard = run.get("hard_mode", False)

    fig = go.Figure(go.Scatter(
        x=positions,
        y=accuracies,
        mode="lines+markers+text",
        line=dict(color="#FF4B4B", width=4),
        marker=dict(color="#FF4B4B", size=14),
        text=[f"{a:.0f}%" for a in accuracies],
        textposition="top center",
        cliponaxis=False,  # keep the label visible above a 100% point
    ))
    fig.update_layout(
        title=f"Accuracy by target position - {run['model']} (N={run['N']}, T={run['T']})"
              + (" (hard mode)" if run_hard else ""),
        xaxis=dict(
            title="Position of target in context",
            type="category",
            categoryorder="array",
            categoryarray=list(POSITIONS),  # First, 25%, Middle, 75%, Last
        ),
        yaxis=dict(title="Accuracy (%)", range=[0, 105]),
    )
    st.plotly_chart(fig)

    # Results table as markdown (no pandas / Arrow needed)
    header = "| Position | Index in list | Correct | Trials | Accuracy (%) | Empty responses |"
    divider = "|---|---|---|---|---|---|"
    if run_hard:
        header += " Fooled by look-alike |"
        divider += "---|"
    rows = [header, divider]
    for r in results:
        row = (
            f"| {r['position']} | {r['index']} | {r['correct']} | {r['trials']} "
            f"| {r['accuracy']:.1f} | {r.get('empty_responses', 0)} |"
        )
        if run_hard:
            row += f" {r.get('fooled_by_lookalike', 0)} |"
        rows.append(row)
    st.markdown("\n".join(rows))
    total_empty = sum(r.get("empty_responses", 0) for r in results)
    if total_empty:
        st.warning(
            f"{total_empty} response(s) came back empty and were counted as wrong "
            "(marked \"EMPTY RESPONSE\" in the saved JSON)."
        )
    st.success(f"Results saved to `{run['path']}`")
