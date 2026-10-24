"""Streamlit UI: `uv run streamlit run ui/app.py`."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))   # `pages` lives next to this file

import pages  # noqa: E402

st.set_page_config(page_title="Prețuri Pisa", page_icon="🛒", layout="wide")
navigation = st.navigation({
    "Cumpărături": [
        st.Page(pages.page_shopping, title="Ce cumpăr și unde", icon="🧺", default=True),
        st.Page(pages.page_catalog, title="Catalog", icon="🔍"),
        st.Page(pages.page_promotions, title="Promoții", icon="🏷️"),
        st.Page(pages.page_lists, title="Liste salvate", icon="📝"),
    ],
    "Întreținere": [
        st.Page(pages.page_health, title="Stare colectare", icon="🩺"),
        st.Page(pages.page_categories, title="Categorii", icon="🗂️"),
        st.Page(pages.page_review, title="Revizuire", icon="🔎"),
        st.Page(pages.page_settings, title="Setări", icon="⚙️"),
    ],
})
navigation.run()
