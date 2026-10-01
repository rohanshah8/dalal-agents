"""Hugging Face Space entry point (copied to the Space root as app.py by the deploy workflow)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app")]
os.environ.setdefault("DALAL_CACHE_DIR", "/tmp/dalal-cache")
os.environ.setdefault("DALAL_LLM_PROVIDER", "none")  # no server-side keys; visitors bring their own

import gradio as gr  # noqa: E402
from gradio_app import CSS, demo  # noqa: E402

demo.launch(theme=gr.themes.Soft(primary_hue="blue"), css=CSS, footer_links=[])
