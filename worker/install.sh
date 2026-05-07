#!/usr/bin/env bash
# FlowUp Alignment Worker — macOS install script
#
# Installs all dependencies needed to run the alignment worker on a Mac Mini
# (Apple Silicon or Intel). Safe to re-run; existing installations are skipped.
#
# Usage:
#   bash install.sh
#
# What it does:
#   1. Checks for / installs Homebrew
#   2. Installs ffmpeg (used by yt-dlp, stable-ts, and Demucs)
#   3. Creates a Python 3.11+ virtual environment in ./worker/.venv
#   4. Installs PyTorch (with MPS support on Apple Silicon, CPU on Intel)
#   5. Installs Demucs
#   6. Installs the Python requirements from requirements.txt
#   7. Copies .env.example → .env if not already present

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Detect architecture
ARCH="$(uname -m)"   # arm64 | x86_64

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║       FlowUp Alignment Worker — macOS Install Script         ║"
echo "║       Architecture: $ARCH"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Homebrew ────────────────────────────────────────────────────────────────
echo "[1/6] Checking Homebrew …"
if ! command -v brew &>/dev/null; then
    echo "  Homebrew not found — installing …"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for the rest of this script
    if [[ "$ARCH" == "arm64" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
else
    echo "  Homebrew already installed: $(brew --version | head -1)"
fi

# ── 2. ffmpeg ──────────────────────────────────────────────────────────────────
echo ""
echo "[2/6] Checking ffmpeg …"
if ! command -v ffmpeg &>/dev/null; then
    echo "  Installing ffmpeg via Homebrew …"
    brew install ffmpeg
else
    echo "  ffmpeg already installed: $(ffmpeg -version 2>&1 | head -1)"
fi

# ── 3. Python virtual environment ──────────────────────────────────────────────
echo ""
echo "[3/6] Setting up Python virtual environment at $VENV_DIR …"

# Prefer Python 3.11 or 3.12 from Homebrew for best PyTorch compatibility.
# Fall back to whatever python3 is on PATH.
PYTHON_CMD=""
for candidate in python3.12 python3.11 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        PY_VER="$("$candidate" -c 'import sys; print(sys.version_info[:2])')"
        # Require at least 3.10
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PYTHON_CMD="$candidate"
            echo "  Using Python: $(command -v "$candidate") ($PY_VER)"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    echo "  Python 3.10+ not found on PATH."
    echo "  Installing Python 3.12 via Homebrew …"
    brew install python@3.12
    PYTHON_CMD="python3.12"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    echo "  Virtual environment created."
else
    echo "  Virtual environment already exists."
fi

PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python"

"$PIP" install --quiet --upgrade pip setuptools wheel

# ── 4. PyTorch ─────────────────────────────────────────────────────────────────
echo ""
echo "[4/6] Installing PyTorch …"

# Check if already installed
if "$PYTHON" -c "import torch" 2>/dev/null; then
    TORCH_VER="$("$PYTHON" -c 'import torch; print(torch.__version__)')"
    echo "  PyTorch already installed: $TORCH_VER"
else
    if [[ "$ARCH" == "arm64" ]]; then
        echo "  Apple Silicon detected — installing PyTorch with MPS support …"
        "$PIP" install --quiet torch torchvision torchaudio
    else
        echo "  Intel Mac detected — installing PyTorch (CPU) …"
        "$PIP" install --quiet torch torchvision torchaudio
    fi
    echo "  PyTorch installed: $("$PYTHON" -c 'import torch; print(torch.__version__)')"
fi

# ── 5. Demucs ──────────────────────────────────────────────────────────────────
echo ""
echo "[5/6] Installing Demucs …"
if "$PYTHON" -c "import demucs" 2>/dev/null; then
    echo "  Demucs already installed: $("$PYTHON" -c 'import demucs; print(demucs.__version__)')"
else
    "$PIP" install --quiet demucs
    echo "  Demucs installed."
fi

# ── 6. Python requirements ─────────────────────────────────────────────────────
echo ""
echo "[6/6] Installing Python requirements from requirements.txt …"
"$PIP" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "  Requirements installed."

# ── .env setup ─────────────────────────────────────────────────────────────────
echo ""
echo "── Configuration ──────────────────────────────────────────────────────────"
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "  Created .env from .env.example"
    echo ""
    echo "  ┌─ NEXT STEPS ──────────────────────────────────────────────────────┐"
    echo "  │  Edit $SCRIPT_DIR/.env                │"
    echo "  │  Set REMOTE_API_URL=https://your-server.example.com              │"
    echo "  │  Set WORKER_API_KEY=<same key as backend WORKER_API_KEY>         │"
    echo "  └───────────────────────────────────────────────────────────────────┘"
else
    echo "  .env already exists — not overwriting."
fi

# ── Smoke test ─────────────────────────────────────────────────────────────────
echo ""
echo "── Smoke test ─────────────────────────────────────────────────────────────"
echo -n "  ffprobe:       " && ffprobe -version 2>&1 | head -1
echo -n "  ffmpeg:        " && ffmpeg  -version 2>&1 | head -1
echo -n "  Python:        " && "$PYTHON" --version
echo -n "  torch:         " && "$PYTHON" -c "import torch; print(torch.__version__, '| MPS:', torch.backends.mps.is_available() if hasattr(torch.backends, 'mps') else 'n/a')"
echo -n "  demucs:        " && "$PYTHON" -c "import demucs; print(demucs.__version__)"
echo -n "  stable-ts:     " && "$PYTHON" -c "import stable_whisper; print(stable_whisper.__version__)"
echo -n "  yt-dlp:        " && "$PYTHON" -m yt_dlp --version
echo -n "  requests:      " && "$PYTHON" -c "import requests; print(requests.__version__)"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Installation complete!                                      ║"
echo "║                                                              ║"
echo "║  Start the worker:                                           ║"
echo "║    .venv/bin/python worker.py                                ║"
echo "║                                                              ║"
echo "║  Process one task and exit:                                  ║"
echo "║    .venv/bin/python worker.py --once                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
