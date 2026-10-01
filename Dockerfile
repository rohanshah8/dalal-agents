# Dalal Agents Streamlit app for self-hosting (Render, Railway, Fly.io, a VPS…).
# The public Hugging Face Space uses the Gradio app instead (see scripts/build_space.sh).
#   docker build -t dalal-agents . && docker run -p 7860:7860 dalal-agents
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DALAL_CACHE_DIR=/tmp/dalal-cache \
    DALAL_LLM_PROVIDER=none \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true

# HF Spaces runs containers as uid 1000
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user pyproject.toml README.md LICENSE ./
COPY --chown=user src ./src
RUN pip install ".[web]"

COPY --chown=user app ./app
COPY --chown=user .streamlit ./.streamlit
USER user

EXPOSE 7860
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/_stcore/health')"
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.port=7860", "--server.address=0.0.0.0"]
