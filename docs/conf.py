from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))


project = "Ad Research Experiment"
author = ""

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build"]

html_theme = "furo"

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Be explicit so CI resolves docs/index.md as the root document.
root_doc = "index"

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "tasklist",
]

autodoc_typehints = "description"

# Keep docs buildable without Playwright/mitmproxy binaries.
autodoc_mock_imports = [
    "playwright",
    "playwright.async_api",
    "mitmproxy",
    "mitmproxy.http",
    "mitmproxy.ctx",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# Avoid locale-related build surprises.
os.environ.setdefault("LC_ALL", "C")
