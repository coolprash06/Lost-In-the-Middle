# Lost in the Middle — U-Curve Tester

A Streamlit app that re-runs the key-value retrieval experiment from Liu et al., ["Lost in the Middle: How Language Models Use Long Contexts"](https://arxiv.org/abs/2307.03172) (TACL 2024), on current models via the Groq API. The paper found that models use information best at the start or end of a long context and worst in the middle, a U-shaped accuracy curve.

## How it works

1. The app builds a JSON object of N key-value pairs, where every key and value is a random UUID, so the model can't know the answer from training.
2. One pair is the target. It is placed at one of five positions: First, 25%, Middle, 75% or Last.
3. The model is asked: *"What is the value for key \<target key\>? Answer with only the value."* (temperature 0).
4. An answer counts as correct if the target's value appears in the reply.
5. This repeats T times per position, and the app plots accuracy per position.

**Hard mode** adds look-alike keys: distractor keys that are copies of the target key with 2–3 characters changed, spread randomly through the list.

## Main finding

Within about 7,300 tokens of context, `openai/gpt-oss-20b` and `qwen/qwen3.8-27b` scored 100% at every position, even in hard mode. `allam-2-7b` scored 0% everywhere because it couldn't copy a UUID correctly. No U-curve was reproduced.

Full write-up: [docs/Lost_in_the_Middle_Report.pdf](docs/Lost_in_the_Middle_Report.pdf)

## Setup (Windows, PowerShell)

You need Python 3.10+ and a free [Groq API key](https://console.groq.com/keys).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env   # then put your Groq API key in .env
python -m streamlit run app.py
```

The app opens in your browser at http://localhost:8501.

> **Note:** On machines with Windows Smart App Control, running `pip.exe` or `streamlit.exe` directly may be blocked, which is why `python -m` is used.
> If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Settings

The sidebar has:

- **Model name**: any Groq model ID (default `openai/gpt-oss-20b`).
- **Number of key-value pairs (N)**: size of the context, 20–500.
- **Trials per position (T)**: 1–10.
- **Tokens-per-minute limit**: your Groq rate limit (default 8,000). The app won't start a run whose single request would exceed it, and paces calls to stay under it.
- **Hard mode (look-alike keys)** and **Number of look-alike keys** (1–30).

## Limitations

- Groq's free tier allows 8,000 tokens per minute, so one request is capped at about 7,300 tokens of context (max ~125 pairs for gpt-oss-20b).
- UUIDs tokenize poorly, at about 1.7 characters per token, so contexts fill up fast.
- The reported runs used 1 trial per position.
- The app paces calls to about 1 per minute, so runs are slow.

## Project structure

```
app.py            Streamlit UI
llm.py            Groq API calls (retries, rate-limit pacing, error handling)
experiments.py    Test logic: building the key-value data, scoring, saving results
results/          Saved runs as JSON
docs/             Report (.pdf/.docx), figures and build_report.py
```
