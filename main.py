import gradio as gr
import os
import re
import html
import random
import pandas as pd
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# =========================================================
# PROJECT PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

DAILY_WISDOM_CSV = str(DATA_DIR / "daily_wisdom.csv")
PROMO_CSV = str(DATA_DIR / "promo_campaigns.csv")

CATALOG_CSV_CANDIDATES = [
    str(DATA_DIR / "Forbidden_Library_Master_Catalog.csv"),
    str(DATA_DIR / "Forbidden_Library_Catalog.csv"),
    str(DATA_DIR / "Forbidden_Library_Master_Catalog.CSV"),
    str(DATA_DIR / "Forbidden_Library_Catalog.CSV"),
]

# =========================================================
# LOCKED DISPLAY TEXT
# =========================================================

BROWSE_HINT = "Type here. Browse the Library Vault by letter, title, keyword, or doctrine."

CATEGORY_GROUPS = [
    ("Alchemy", ["alchemy", "alchemical"]),
    ("Hermeticism", ["hermetic", "hermeticism", "hermes", "kybalion"]),
    ("Magic", ["magic", "magick", "sorcery", "occult", "occultism"]),
    ("Mysticism", ["mysticism", "mystic", "gnosis", "gnostic"]),
    ("Religion", ["religion", "religious", "christian", "judaism", "islam", "bible", "god"]),
    ("Mythology", ["mythology", "myth", "egyptian", "greek", "roman", "norse", "babylonian", "sumerian"]),
    ("Early Science", ["science", "early science", "natural philosophy", "tesla", "physics", "chemistry"]),
    ("Philosophy", ["philosophy", "philosophical", "metaphysics", "ethics", "logic"]),
]

TOP_CATEGORIES = [name for name, _ in CATEGORY_GROUPS]

# =========================================================
# CLEANERS
# =========================================================

def _clean_display_text(text: str) -> str:
    t = html.unescape(str(text or ""))

    replacements = {
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
        "â€": '"',
        "â€“": "-",
        "â€”": "—",
        "â€¦": "...",
        "Â ": " ",
        "Â": "",
        "Ã©": "e",
        "Ã¨": "e",
        "Ã": "",
        "�": "",
    }

    for bad, good in replacements.items():
        t = t.replace(bad, good)

    t = re.sub(r"[øØðÐþÞœŒæÆ]+", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)

    return t.strip()


def normalize_space(text):
    text = str(text or "").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_bool(val, default=False):
    if val is None:
        return default

    s = str(val).strip().lower()

    if s in {"true", "1", "yes", "y", "active"}:
        return True

    if s in {"false", "0", "no", "n", "inactive"}:
        return False

    return default


def _safe_row_text(row, col):
    if col in row and pd.notna(row[col]):
        return str(row[col]).strip()
    return ""


def _safe_int(val, default=5):
    try:
        n = int(float(str(val).strip()))
        return n if n > 0 else default
    except:
        return default


# =========================================================
# PDF / DRIVE HELPERS
# =========================================================

def _extract_drive_file_id_from_url(url):
    s = str(url or "").strip()

    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
    ]

    for p in patterns:
        m = re.search(p, s)
        if m:
            return m.group(1)

    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", s):
        return s

    return ""


def _normalize_pdf_link(val):
    link = str(val or "").strip()

    if not link:
        return ""

    if "drive.google.com/file/d/" in link:
        return link

    if "drive.google.com/open?id=" in link:
        file_id = link.split("open?id=")[-1].split("&")[0].strip()
        if file_id:
            return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    if "drive.google.com/uc?id=" in link:
        file_id = link.split("uc?id=")[-1].split("&")[0].strip()
        if file_id:
            return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", link):
        return f"https://drive.google.com/file/d/{link}/view?usp=sharing"

    if link.startswith("http://") or link.startswith("https://"):
        return link

    return ""


def _build_drive_thumbnail_url(link):
    file_id = _extract_drive_file_id_from_url(link)
    if not file_id:
        return ""
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"


def _normalize_cover_image(val):
    link = str(val or "").strip()

    if not link:
        return ""

    link = link.replace("[/img]", "").strip()

    if link.startswith("http://") or link.startswith("https://"):
        return link

    return ""


def _normalize_public_image_url(url):
    s = str(url or "").strip().replace("[/img]", "").strip()

    if not s:
        return ""

    if "drive.google.com" in s:
        file_id = _extract_drive_file_id_from_url(s)
        if file_id:
            return f"https://drive.google.com/uc?export=view&id={file_id}"

    if s.startswith("http://") or s.startswith("https://"):
        return s

    return ""


def _normalize_target_url(url):
    s = str(url or "").strip()

    if not s:
        return ""

    if s.startswith("http://") or s.startswith("https://"):
        return s

    return ""


def build_pdf_viewer_html(pdf_url):
    pdf_url = str(pdf_url or "").strip()

    if not pdf_url:
        return """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Reader</div>
            <div class="body_text">No PDF link was available for this title.</div>
          </div>
        </div>
        """

    file_id = _extract_drive_file_id_from_url(pdf_url)

    if file_id:
        embed_url = f"https://drive.google.com/file/d/{file_id}/preview"
    else:
        embed_url = pdf_url

    safe_embed = html.escape(embed_url, quote=True)
    safe_link = html.escape(pdf_url, quote=True)

    return f"""
    <div class="results_wrap">
      <div class="card">
        <div class="card_title">Reader</div>

        <div class="pdf_mobile_fallback">
          <a href="{safe_link}" target="_blank" rel="noopener noreferrer" class="pdf_fallback_btn">
            Open PDF in Browser
          </a>
        </div>

        <div class="pdf_viewer_wrap pdf_desktop_embed">
          <iframe
            src="{safe_embed}"
            class="pdf_viewer_iframe"
            allow="autoplay"
            loading="lazy">
          </iframe>
        </div>
      </div>
    </div>
    """


# =========================================================
# CATALOG LOADER
# =========================================================

_catalog_df_cache = None


def find_catalog_csv():
    for path in CATALOG_CSV_CANDIDATES:
        if os.path.exists(path):
            return path

    raise ValueError(
        "Catalog CSV not found. Expected one of: "
        + ", ".join(CATALOG_CSV_CANDIDATES)
    )


def load_catalog_df():
    global _catalog_df_cache

    if _catalog_df_cache is not None:
        return _catalog_df_cache

    catalog_csv = find_catalog_csv()
    df = pd.read_csv(catalog_csv)
    df.columns = [str(c).strip() for c in df.columns]
    _catalog_df_cache = df

    print("CATALOG SOURCE:", catalog_csv)
    print("CATALOG ROWS:", len(df))

    return df


def _book_dedupe_key(book):
    b = str(book or "").lower().strip()
    b = re.sub(r"\(\d+\)$", "", b).strip()
    b = re.sub(r"[_\-]+", " ", b)
    b = re.sub(r"\.pdf$", "", b, flags=re.IGNORECASE)
    b = re.sub(r"\s+", " ", b).strip()
    return b


def build_catalog_books():
    df = load_catalog_df()
    books = []
    seen = set()

    for _, row in df.iterrows():
        title = (
            _safe_row_text(row, "Title_Display")
            or _safe_row_text(row, "Title")
            or _safe_row_text(row, "PDF_File_Name")
        ).strip()

        if not title:
            continue

        title = re.sub(r"\.pdf$", "", title, flags=re.IGNORECASE).strip()
        title = _clean_display_text(title)
        dedupe_key = _book_dedupe_key(title)

        if not dedupe_key or dedupe_key in seen:
            continue

        seen.add(dedupe_key)

        full_pdf_link = _normalize_pdf_link(
            _safe_row_text(row, "Full_PDF_Drive_Link")
            or _safe_row_text(row, "PDF_Drive_Link")
        )

        preview_pdf_link = _normalize_pdf_link(_safe_row_text(row, "Preview_PDF_Drive_Link"))

        # Free version: use full PDF first. If only preview exists, use preview.
        readable_link = full_pdf_link or preview_pdf_link

        cover_link = _normalize_cover_image(_safe_row_text(row, "Cover_Image"))

        if not cover_link:
            cover_link = _build_drive_thumbnail_url(readable_link)

        search_parts = [
            title,
            _safe_row_text(row, "Alternate Title"),
            _safe_row_text(row, "Author"),
            _safe_row_text(row, "Category"),
            _safe_row_text(row, "Subcategory"),
            _safe_row_text(row, "Tradition"),
            _safe_row_text(row, "Keywords"),
            _safe_row_text(row, "Short Description"),
            _safe_row_text(row, "Long Description"),
        ]

        books.append({
            "title": title,
            "title_low": title.lower(),
            "url": readable_link,
            "thumb": cover_link,
            "category": _safe_row_text(row, "Category"),
            "tradition": _safe_row_text(row, "Tradition"),
            "author": _safe_row_text(row, "Author"),
            "description": _safe_row_text(row, "Short Description"),
            "search_text": normalize_space(" ".join(search_parts)).lower()
        })

    return books


catalog_books = build_catalog_books()


# =========================================================
# DAILY JEWEL
# =========================================================

def load_jewel_df():
    if not os.path.exists(DAILY_WISDOM_CSV):
        print("No Daily Wisdom CSV found:", DAILY_WISDOM_CSV)
        return pd.DataFrame(columns=["Quote", "Author", "Book"])

    df = pd.read_csv(DAILY_WISDOM_CSV)
    df.columns = [str(c).strip() for c in df.columns]

    if "Quote" not in df.columns:
        print("daily_wisdom.csv missing Quote column.")
        return pd.DataFrame(columns=["Quote", "Author", "Book"])

    rows = []

    for _, row in df.iterrows():
        quote = normalize_space(row.get("Quote", ""))

        if not quote:
            continue

        author = str(row.get("Author", "") or "").strip()
        book = str(row.get("Book", "") or "").strip()

        if not author:
            author = "Unknown"

        if not book:
            book = "Unknown Source"

        rows.append({
            "Quote": quote,
            "Author": author,
            "Book": book
        })

    out = pd.DataFrame(rows).drop_duplicates(subset=["Quote"]).reset_index(drop=True)
    print("JEWELS LOADED:", len(out))
    return out


jewel_df = load_jewel_df()


def format_daily_jewel(row):
    quote = html.escape(str(row["Quote"]).strip())
    author = html.escape(str(row["Author"]).strip() or "Unknown")
    book = html.escape(str(row["Book"]).strip() or "Unknown Source")

    return f"""
    <div class="side_wrap">
      <div class="card">
        <div class="card_title">Daily Jewel</div>
        <div class="daily_jewel_quote_wrap">
          <div class="daily_jewel_quote">
            <span class="daily_jewel_quote_inline_mark">“</span>{quote}<span class="daily_jewel_quote_inline_mark">”</span>
          </div>
        </div>
        <div class="daily_jewel_meta">
          <div class="daily_jewel_author">{author}</div>
          <div class="daily_jewel_book">{book}</div>
        </div>
      </div>
    </div>
    """


def refresh_jewel():
    if jewel_df.empty:
        return """
        <div class="side_wrap">
          <div class="card">
            <div class="card_title">Daily Jewel</div>
            <div class="body_text">No jewels available.</div>
          </div>
        </div>
        """

    idx = random.choice(list(jewel_df.index))
    return format_daily_jewel(jewel_df.loc[idx])


# =========================================================
# PROMO ROTATOR
# =========================================================

def load_promo_df():
    if not os.path.exists(PROMO_CSV):
        return pd.DataFrame(columns=[
            "Promo_ID", "Promo_Name", "Image_URL", "Target_URL",
            "Duration_Seconds", "Active", "Priority", "Start_Date",
            "End_Date", "Notes"
        ])

    df = pd.read_csv(PROMO_CSV)
    df.columns = [str(c).strip() for c in df.columns]

    required = [
        "Promo_ID", "Promo_Name", "Image_URL", "Target_URL",
        "Duration_Seconds", "Active", "Priority", "Start_Date", "End_Date"
    ]

    for col in required:
        if col not in df.columns:
            raise ValueError(f"promo_campaigns.csv is missing required column: {col}")

    today = date.today().isoformat()
    rows = []

    for _, row in df.iterrows():
        active = str(row.get("Active", "")).strip().lower()

        if active not in {"yes", "true", "1", "active"}:
            continue

        start_date = str(row.get("Start_Date", "")).strip()
        end_date = str(row.get("End_Date", "")).strip()

        if start_date and today < start_date:
            continue

        if end_date and today > end_date:
            continue

        image_url = _normalize_public_image_url(row.get("Image_URL", ""))
        target_url = _normalize_target_url(row.get("Target_URL", ""))

        if not target_url:
            continue

        rows.append({
            "Promo_ID": str(row.get("Promo_ID", "")).strip(),
            "Promo_Name": str(row.get("Promo_Name", "")).strip() or "Promotion",
            "Image_URL": image_url,
            "Target_URL": target_url,
            "Duration_Seconds": _safe_int(row.get("Duration_Seconds", 5), 5),
            "Priority": _safe_int(row.get("Priority", 999), 999),
        })

    out = pd.DataFrame(rows)

    if out.empty:
        return out

    out = out.sort_values(
        by=["Priority", "Promo_ID"],
        ascending=[True, True]
    ).reset_index(drop=True)

    return out


def make_promo_state():
    promo_df = load_promo_df()

    if promo_df.empty:
        return {
            "rows": [],
            "index": 0
        }

    rows = []

    for _, row in promo_df.iterrows():
        rows.append({
            "Promo_ID": str(row["Promo_ID"]).strip(),
            "Promo_Name": str(row["Promo_Name"]).strip(),
            "Image_URL": str(row["Image_URL"]).strip(),
            "Target_URL": str(row["Target_URL"]).strip(),
            "Duration_Seconds": _safe_int(row["Duration_Seconds"], 5)
        })

    return {
        "rows": rows,
        "index": 0
    }


def build_single_promo_html(promo):
    if not promo:
        return """
        <div class="promo_shell">
          <div class="card promo_card">
            <div class="card_title">Featured Promotion</div>
            <div class="promo_stage promo_stage_empty">
              <div class="promo_empty_text">Promo space available.</div>
            </div>
          </div>
        </div>
        """

    name = html.escape(str(promo.get("Promo_Name", "Promotion")).strip())
    image = html.escape(str(promo.get("Image_URL", "")).strip(), quote=True)
    target = html.escape(str(promo.get("Target_URL", "")).strip(), quote=True)

    if image:
        stage_inner = f'<img class="promo_img" src="{image}" alt="{name}">'
    else:
        stage_inner = f'<div class="promo_fallback">{name}</div>'

    return f"""
    <div class="promo_shell">
      <div class="card promo_card">
        <div class="card_title">Featured Promotion</div>
        <a class="promo_link" href="{target}" target="_blank" rel="noopener noreferrer">
          <div class="promo_stage">
            {stage_inner}
          </div>
          <div class="promo_meta">
            <div class="promo_name">{name}</div>
          </div>
        </a>
      </div>
    </div>
    """


def rotate_promo(promo_state):
    if not promo_state or not promo_state.get("rows"):
        empty_html = build_single_promo_html(None)
        return empty_html, {"rows": [], "index": 0}

    rows = promo_state["rows"]
    idx = int(promo_state.get("index", 0) or 0)

    if idx >= len(rows):
        idx = 0

    promo = rows[idx]
    html_out = build_single_promo_html(promo)

    next_idx = (idx + 1) % len(rows)

    return html_out, {
        "rows": rows,
        "index": next_idx
    }


# =========================================================
# FEATURED SHELF
# =========================================================

def build_featured_shelf_html():
    books_with_images = [b for b in catalog_books if b.get("thumb")]

    if books_with_images:
        picks = random.sample(books_with_images, min(3, len(books_with_images)))
    else:
        picks = random.sample(catalog_books, min(3, len(catalog_books))) if catalog_books else []

    if not picks:
        return """
        <div class="featured_wrap">
          <div class="featured_row">
            <div class="featured_empty">No featured books available.</div>
          </div>
        </div>
        """

    cards = []

    for book in picks:
        title = html.escape(book["title"])
        thumb = html.escape(book["thumb"], quote=True) if book["thumb"] else ""

        if thumb:
            card_html = f"""
            <div class="featured_card">
              <div class="featured_cover">
                <img src="{thumb}" alt="{title}" class="featured_img" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'; this.parentNode.classList.add('featured_cover_fallback_active'); this.parentNode.innerHTML='<div class=&quot;featured_fallback_text&quot;>' + this.alt + '</div>';">
              </div>
              <div class="featured_title">{title}</div>
            </div>
            """
        else:
            card_html = f"""
            <div class="featured_card">
              <div class="featured_cover featured_cover_fallback_active">
                <div class="featured_fallback_text">{title}</div>
              </div>
              <div class="featured_title">{title}</div>
            </div>
            """

        cards.append(card_html)

    return f"""
    <div class="featured_wrap">
      <div class="featured_row">
        {''.join(cards)}
      </div>
    </div>
    """


# =========================================================
# SEARCH / CATEGORY LOGIC
# =========================================================

def build_selector_choices(matches):
    if not matches:
        return gr.update(choices=[], value=None)

    choices = []

    for book in matches:
        title = str(book.get("title", "")).strip()

        if title:
            choices.append(title)

    return gr.update(choices=choices, value=None)


def _book_matches_category(book, category_terms):
    fields = [
        str(book.get("title", "")),
        str(book.get("category", "")),
        str(book.get("tradition", "")),
        str(book.get("author", "")),
        str(book.get("search_text", "")),
    ]

    hay = " ".join(fields).lower()

    for term in category_terms:
        t = term.lower().strip()

        if not t:
            continue

        if re.search(rf"\b{re.escape(t)}\b", hay) or t in hay:
            return True

    return False


def browse_matrix_category(category_name):
    q = _clean_display_text(category_name).strip()

    if not q:
        return gr.update(choices=[], value=None)

    category_terms = None

    for label, terms in CATEGORY_GROUPS:
        if label.lower() == q.lower():
            category_terms = terms
            break

    if category_terms is None:
        category_terms = [q.lower()]

    matches = [
        b for b in catalog_books
        if _book_matches_category(b, category_terms)
    ]

    matches = sorted(matches, key=lambda b: b["title_low"])[:200]

    return build_selector_choices(matches)


def browse_matrix_vault(query):
    q = str(query or "").strip()
    q_low = q.lower()

    if not q_low:
        return gr.update(choices=[], value=None)

    if len(q_low) == 1 and q_low.isalpha():
        matches = [
            b for b in sorted(catalog_books, key=lambda b: b["title_low"])
            if b["title_low"].startswith(q_low)
        ][:200]
    else:
        q_tokens = [t for t in re.findall(r"[a-zA-Z0-9']+", q_low) if len(t) > 1]

        ranked = []

        for book in catalog_books:
            title_low = book["title_low"]
            hay = book["search_text"]
            score = 0

            if q_low in title_low:
                score += 200

            if q_low in hay:
                score += 80

            for tok in q_tokens:
                if re.search(rf"\b{re.escape(tok)}\b", title_low):
                    score += 30
                elif tok in title_low:
                    score += 12

                if re.search(rf"\b{re.escape(tok)}\b", hay):
                    score += 10
                elif tok in hay:
                    score += 4

            if score > 0:
                ranked.append((score, book))

        ranked.sort(key=lambda x: (-x[0], x[1]["title_low"]))
        matches = [book for _, book in ranked[:200]]

    return build_selector_choices(matches)


def select_book_by_title(title):
    clean_title = _clean_display_text(title).strip()

    if not clean_title:
        return {
            "title": "",
            "url": "",
        }

    for book in catalog_books:
        if str(book.get("title", "")).strip().lower() == clean_title.lower():
            return {
                "title": book.get("title", ""),
                "url": book.get("url", ""),
            }

    return {
        "title": "",
        "url": "",
    }


def open_selected_book(selected_book):
    selected_book = selected_book or {}

    title = str(selected_book.get("title", "")).strip()
    url = str(selected_book.get("url", "")).strip()

    if not title:
        return """
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">Select a Title</div>
            <div class="body_text">Choose a book from the browse results first.</div>
          </div>
        </div>
        """

    if not url:
        safe_title = html.escape(title)
        return f"""
        <div class="results_wrap">
          <div class="card">
            <div class="card_title">PDF Unavailable</div>
            <div class="body_text">No PDF link was available for <b>{safe_title}</b>.</div>
          </div>
        </div>
        """

    return build_pdf_viewer_html(url)


def refresh_vault_panels():
    return refresh_jewel(), build_featured_shelf_html()


# =========================================================
# CSS
# =========================================================

CUSTOM_CSS = """
:root {
  --bg-1: #020617;
  --bg-2: #0f172a;
  --card: rgba(15,23,42,0.96);
  --card-2: rgba(30,41,59,0.94);
  --line: rgba(148,163,184,0.16);
  --text: #f8fafc;
  --muted: #cbd5e1;
  --soft: #94a3b8;

  --browse: #0f766e;
  --browse-hover: #0d5f59;

  --refresh: #7c3aed;
  --refresh-hover: #6d28d9;
}

html, body, .gradio-container {
  margin: 0 !important;
  padding: 0 !important;
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
  background: radial-gradient(circle at top, #0f172a 0%, #020617 45%, #000000 100%) !important;
  color: var(--text) !important;
  font-family: Inter, Arial, sans-serif !important;
}

.gradio-container {
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
}

#app_shell {
  width: 100% !important;
  max-width: 760px !important;
  margin: 0 auto !important;
  padding: 14px !important;
  box-sizing: border-box !important;
  overflow-x: hidden !important;
}

#app_shell * {
  max-width: 100%;
  box-sizing: border-box;
}

.title_wrap {
  border: 1px solid rgba(148,163,184,0.16);
  border-radius: 18px;
  background: linear-gradient(135deg, rgba(37,99,235,0.14), rgba(15,23,42,0.96));
  padding: 18px 14px 16px 14px;
  margin-bottom: 16px;
  text-align: center;
  box-shadow: 0 10px 24px rgba(0,0,0,0.24);
}

.title_main {
  font-size: 30px;
  font-weight: 900;
  line-height: 1.05;
  margin-bottom: 8px;
  color: #ffffff;
}

.title_tag {
  font-size: 13px;
  line-height: 1.5;
  color: var(--muted);
  max-width: 100%;
  margin: 0 auto;
}

.free_access_wrap {
  border: 1px solid rgba(20,184,166,0.24);
  border-radius: 16px;
  background: rgba(15,118,110,0.14);
  padding: 12px;
  margin-bottom: 14px;
  text-align: center;
}

.free_access_title {
  font-size: 17px;
  font-weight: 900;
  color: #ffffff;
  margin-bottom: 4px;
}

.free_access_text {
  color: #cbd5e1;
  font-size: 13px;
  line-height: 1.45;
}

#query_box {
  width: 100% !important;
  margin-bottom: 14px !important;
}

#query_box > div,
#query_box .wrap,
#query_box .block,
#query_box .gr-box,
#query_box .gr-input,
#query_box textarea {
  width: 100% !important;
}

#query_box textarea {
  display: block !important;
  min-height: 58px !important;
  height: 58px !important;
  max-height: 90px !important;
  visibility: visible !important;
  opacity: 1 !important;
  resize: none !important;
  overflow-y: auto !important;
  background: rgba(30,41,59,0.86) !important;
  color: #e5e7eb !important;
  border: 1px solid rgba(148,163,184,0.18) !important;
  border-radius: 12px !important;
  font-size: 14px !important;
  font-weight: 500 !important;
  line-height: 1.35 !important;
  padding: 11px 14px !important;
  box-sizing: border-box !important;
}

#query_box textarea::placeholder {
  color: #cbd5e1 !important;
  opacity: 1 !important;
}

#browse_btn,
#browse_btn button {
  background: var(--browse) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
}

#browse_btn:hover,
#browse_btn button:hover {
  background: var(--browse-hover) !important;
}

#refresh_btn,
#refresh_btn button {
  background: var(--refresh) !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
}

#refresh_btn:hover,
#refresh_btn button:hover {
  background: var(--refresh-hover) !important;
}

#open_pdf_btn,
#open_pdf_btn button {
  background: #dc2626 !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 800 !important;
  margin-bottom: 10px !important;
}

#open_pdf_btn:hover,
#open_pdf_btn button:hover {
  background: #b91c1c !important;
}

.category_section_label {
  font-size: 16px;
  font-weight: 800;
  color: #ffffff;
  margin: 18px 0 10px 0;
  text-align: center !important;
  width: 100% !important;
}

#category_button_stack {
  display: flex !important;
  flex-direction: column !important;
  gap: 6px !important;
  margin-bottom: 12px !important;
}

#category_button_row {
  display: flex !important;
  gap: 8px !important;
  margin: 0 !important;
  padding: 0 !important;
  justify-content: center !important;
}

.category_btn,
.category_btn button {
  background: #991b1b !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 10px !important;
  font-weight: 800 !important;
  font-size: 12px !important;
  line-height: 1 !important;
  height: 32px !important;
  min-height: 32px !important;
  padding: 0 8px !important;
  box-shadow: 0 6px 14px rgba(0,0,0,0.18) !important;
}

.category_btn:hover,
.category_btn button:hover {
  background: #7f1d1d !important;
  color: #ffffff !important;
}

#result_selector_radio {
  margin: 14px 0 14px 0 !important;
}

#result_selector_radio .wrap,
#result_selector_radio .block,
#result_selector_radio fieldset {
  border: 1px solid rgba(148,163,184,0.16) !important;
  border-radius: 16px !important;
  background: rgba(15,23,42,0.96) !important;
  padding: 12px !important;
  max-height: 420px !important;
  overflow-y: auto !important;
}

#result_selector_radio label {
  background: rgba(30,41,59,0.94) !important;
  border: 1px solid rgba(148,163,184,0.14) !important;
  border-radius: 12px !important;
  padding: 12px 14px !important;
  margin-bottom: 8px !important;
  color: #ffffff !important;
  font-weight: 700 !important;
  line-height: 1.35 !important;
}

#result_selector_radio label[data-selected="true"],
#result_selector_radio label:has(input[type="radio"]:checked) {
  background: rgba(185,28,28,0.92) !important;
  border: 1px solid rgba(239,68,68,0.75) !important;
  color: #ffffff !important;
}

#result_selector_radio input[type="radio"] {
  accent-color: #dc2626 !important;
}

.featured_wrap {
  width: 100%;
  margin: 18px 0 12px 0;
}

.featured_row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.featured_card {
  text-decoration: none !important;
  display: block;
}

.featured_cover {
  width: 100%;
  aspect-ratio: 0.72 / 1;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid rgba(148,163,184,0.18);
  background: rgba(30,41,59,0.94);
  box-shadow: 0 10px 24px rgba(0,0,0,0.22);
  display: flex;
  align-items: center;
  justify-content: center;
}

.featured_img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  background: rgba(30,41,59,0.94);
}

.featured_cover_fallback_active {
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 10px !important;
  text-align: center !important;
  background: linear-gradient(180deg, rgba(37,99,235,0.18), rgba(30,41,59,0.96)) !important;
}

.featured_fallback_text {
  font-size: 13px;
  line-height: 1.35;
  font-weight: 700;
  color: #ffffff;
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.featured_title {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.35;
  color: #e2e8f0;
  text-align: center;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 32px;
}

.featured_empty {
  width: 100%;
  text-align: center;
  color: var(--soft);
  font-size: 13px;
  padding: 10px 0;
}

.results_wrap,
.side_wrap {
  border: 1px solid rgba(148,163,184,0.16);
  border-radius: 16px;
  background: rgba(15,23,42,0.96);
  padding: 10px;
  box-shadow: 0 10px 24px rgba(0,0,0,0.22);
  margin-bottom: 12px;
}

.card {
  border: 1px solid rgba(148,163,184,0.14);
  border-radius: 14px;
  background: rgba(30,41,59,0.94);
  padding: 12px;
  margin-bottom: 10px;
}

.card_title {
  font-size: 18px;
  font-weight: 800;
  margin-bottom: 8px;
  color: #ffffff;
}

.body_text {
  color: #ffffff;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.daily_jewel_quote_wrap {
  text-align: center;
  margin: 4px 0 8px 0;
}

.daily_jewel_quote {
  font-size: 16px;
  line-height: 1.55;
  color: #ffffff;
  text-align: center;
  white-space: normal;
  margin: 0;
}

.daily_jewel_quote_inline_mark {
  color: #cbd5e1;
  opacity: 0.95;
  font-size: 18px;
  font-weight: 700;
}

.daily_jewel_meta {
  text-align: center;
  margin-top: 6px;
}

.daily_jewel_author {
  font-size: 15px;
  font-weight: 800;
  color: #ffffff;
  margin-bottom: 2px;
}

.daily_jewel_book {
  font-size: 14px;
  color: #cbd5e1;
}

.promo_shell {
  width: 100%;
  margin: 12px 0 12px 0;
}

.promo_card {
  padding: 12px;
}

.promo_link {
  display: block;
  text-decoration: none !important;
}

.promo_stage {
  width: 100%;
  aspect-ratio: 1.55 / 1;
  min-height: 0;
  max-height: 520px;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid rgba(148,163,184,0.16);
  background: rgba(30,41,59,0.94);
  box-shadow: 0 10px 24px rgba(0,0,0,0.22);
  display: flex;
  align-items: center;
  justify-content: center;
}

.promo_img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  display: block;
  background: rgba(30,41,59,0.94);
}

.promo_fallback {
  width: 100%;
  min-height: 220px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 18px;
  color: #ffffff;
  font-size: 20px;
  font-weight: 800;
  line-height: 1.3;
  background: linear-gradient(180deg, rgba(37,99,235,0.18), rgba(30,41,59,0.96));
}

.promo_stage_empty {
  min-height: 140px;
}

.promo_empty_text {
  color: #cbd5e1;
  font-size: 16px;
  font-weight: 700;
  text-align: center;
  padding: 20px;
}

.promo_meta {
  padding-top: 10px;
  text-align: center;
}

.promo_name {
  color: #ffffff;
  font-size: 15px;
  font-weight: 800;
  line-height: 1.35;
}

.pdf_mobile_fallback {
  display: none;
  margin-bottom: 12px;
}

.pdf_fallback_btn {
  display: inline-block;
  background: #dc2626;
  color: #ffffff !important;
  text-decoration: none !important;
  border-radius: 12px;
  padding: 12px 16px;
  font-weight: 800;
}

.pdf_fallback_btn:hover {
  background: #b91c1c;
}

.pdf_viewer_wrap {
  width: 100%;
  border: 1px solid rgba(148,163,184,0.16);
  border-radius: 14px;
  overflow: hidden;
  background: rgba(2,6,23,0.95);
}

.pdf_viewer_iframe {
  width: 100%;
  height: 620px;
  border: none;
  display: block;
  background: #ffffff;
}

@media (max-width: 767px) {
  #app_shell {
    max-width: 430px !important;
    padding: 10px !important;
  }

  .title_main {
    font-size: 24px;
  }

  .title_tag {
    font-size: 11px;
    line-height: 1.45;
  }

  #query_box textarea {
    min-height: 58px !important;
    height: 58px !important;
    max-height: 90px !important;
    font-size: 14px !important;
    padding: 11px 14px !important;
  }

  #category_button_stack {
    gap: 5px !important;
    margin-bottom: 10px !important;
  }

  #category_button_row {
    gap: 6px !important;
    justify-content: center !important;
  }

  .category_btn,
  .category_btn button {
    font-size: 12px !important;
    height: 30px !important;
    min-height: 30px !important;
    padding: 0 6px !important;
  }

  #result_selector_radio .wrap,
  #result_selector_radio .block,
  #result_selector_radio fieldset {
    max-height: 360px !important;
    padding: 10px !important;
  }

  #result_selector_radio label {
    padding: 10px 12px !important;
    font-size: 15px !important;
  }

  .featured_row {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .featured_title {
    font-size: 11px;
    min-height: 30px;
  }

  .pdf_mobile_fallback {
    display: block;
  }

  .pdf_desktop_embed {
    display: none;
  }

  .promo_stage {
    aspect-ratio: 1.55 / 1;
    max-height: 320px;
  }

  .promo_fallback {
    min-height: 180px;
    font-size: 18px;
    padding: 14px;
  }

  .promo_name {
    font-size: 14px;
  }
}

/* =========================================================
   WEGOTUSTV CINEMATIC RESKIN
   ========================================================= */

:root {
  --wgutv-black: #020202;
  --wgutv-deep: #050505;
  --wgutv-panel: rgba(10, 12, 16, 0.88);
  --wgutv-panel-soft: rgba(17, 20, 28, 0.82);
  --wgutv-red: #ef1d24;
  --wgutv-red-dark: #8f1117;
  --wgutv-gold: #9a7a4a;
  --wgutv-border: rgba(255,255,255,0.10);
}

html,
body,
.gradio-container {
  background:
    radial-gradient(circle at 18% 8%, rgba(27, 55, 95, 0.28), transparent 28%),
    radial-gradient(circle at 92% 10%, rgba(105, 16, 20, 0.28), transparent 32%),
    radial-gradient(circle at 50% 0%, rgba(255,255,255,0.045), transparent 28%),
    linear-gradient(180deg, #050505 0%, #010101 45%, #000000 100%) !important;
  color: #ffffff !important;
}

.gradio-container::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  opacity: 0.22;
  background-image:
    repeating-linear-gradient(
      0deg,
      rgba(255,255,255,0.035) 0px,
      rgba(255,255,255,0.035) 1px,
      transparent 1px,
      transparent 3px
    ),
    repeating-linear-gradient(
      90deg,
      rgba(255,255,255,0.02) 0px,
      rgba(255,255,255,0.02) 1px,
      transparent 1px,
      transparent 4px
    );
  mix-blend-mode: overlay;
}

.gradio-container::after {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    linear-gradient(90deg, rgba(0,0,0,0.96), rgba(0,0,0,0.28) 42%, rgba(0,0,0,0.92)),
    radial-gradient(circle at center, transparent 0%, rgba(0,0,0,0.72) 76%);
}

#app_shell {
  position: relative !important;
  z-index: 2 !important;
  max-width: 980px !important;
  padding: 18px 16px 32px !important;
}

.title_wrap {
  position: relative;
  overflow: hidden;
  min-height: 230px;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 20px;
  background:
    linear-gradient(90deg, rgba(0,0,0,0.92) 0%, rgba(3,6,12,0.74) 45%, rgba(24,5,7,0.36) 100%),
    radial-gradient(circle at 24% 20%, rgba(49, 92, 140, 0.18), transparent 34%),
    radial-gradient(circle at 88% 16%, rgba(145, 18, 24, 0.28), transparent 34%),
    #030303;
  padding: 38px 18px 34px 18px;
  margin-bottom: 18px;
  text-align: left;
  box-shadow:
    0 24px 70px rgba(0,0,0,0.62),
    inset 0 1px 0 rgba(255,255,255,0.08);
}

.title_wrap::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(180deg, rgba(255,255,255,0.035), transparent 24%),
    linear-gradient(90deg, rgba(0,0,0,0.20), rgba(0,0,0,0.38));
  z-index: 1;
}

.vault_logo_backdrop {
  position: absolute;
  inset: -40px -90px -50px -90px;
  z-index: 0;
  background-image: url("/assets/WGULOGO.png");
  background-repeat: no-repeat;
  background-position: center center;
  background-size: min(115%, 1050px);
  opacity: 0.62;
  filter: brightness(0.72) contrast(1.22) saturate(0.95);
  mix-blend-mode: normal;
  pointer-events: none;
}

.vault_logo_backdrop::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(0,0,0,0.72), rgba(0,0,0,0.22), rgba(0,0,0,0.72)),
    linear-gradient(180deg, rgba(0,0,0,0.08), rgba(0,0,0,0.58));
  pointer-events: none;
}

.title_content {
  position: relative;
  z-index: 2;
  max-width: 720px;
}

.title_eyebrow {
  margin: 0 0 12px 0;
  color: #ff4b55;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.42em;
  text-transform: uppercase;
}

.title_main {
  font-size: clamp(34px, 6vw, 58px);
  font-weight: 950;
  line-height: 0.98;
  letter-spacing: -0.045em;
  margin-bottom: 14px;
  color: #ffffff;
  text-shadow: 0 10px 28px rgba(0,0,0,0.72);
}

.title_tag {
  font-size: 15px;
  line-height: 1.55;
  color: rgba(255,255,255,0.82);
  max-width: 640px;
  margin: 0;
  text-shadow: 0 8px 20px rgba(0,0,0,0.72);
}

.free_access_wrap {
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 16px;
  background:
    linear-gradient(135deg, rgba(10,12,16,0.92), rgba(17,22,33,0.62)),
    radial-gradient(circle at 80% 0%, rgba(239,29,36,0.16), transparent 38%);
  padding: 14px 12px;
  margin-bottom: 16px;
  text-align: center;
  box-shadow:
    0 16px 40px rgba(0,0,0,0.42),
    inset 0 1px 0 rgba(255,255,255,0.06);
}

.free_access_title {
  font-size: 18px;
  font-weight: 950;
  color: #ffffff;
  margin-bottom: 4px;
}

.free_access_text {
  color: rgba(255,255,255,0.72);
  font-size: 13px;
  line-height: 1.45;
}

#query_box,
#result_selector_radio,
.results_wrap,
.side_wrap,
.promo_card {
  filter: drop-shadow(0 18px 34px rgba(0,0,0,0.34));
}

#query_box .wrap,
#query_box .block,
#query_box textarea {
  background:
    linear-gradient(180deg, rgba(18,21,29,0.92), rgba(9,11,16,0.92)) !important;
  border-color: rgba(255,255,255,0.11) !important;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.065),
    0 18px 40px rgba(0,0,0,0.32) !important;
}

#query_box textarea {
  min-height: 54px !important;
  height: 54px !important;
  max-height: 82px !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  letter-spacing: 0.01em !important;
  color: rgba(255,255,255,0.78) !important;
  padding: 10px 13px !important;
}

#query_box textarea::placeholder {
  color: rgba(255,255,255,0.62) !important;
}

#browse_btn,
#browse_btn button {
  background: linear-gradient(135deg, #0aa58f, #077263) !important;
  box-shadow: 0 16px 34px rgba(0,0,0,0.34) !important;
}

.category_btn,
.category_btn button {
  background: linear-gradient(135deg, #ef1d24, #991116) !important;
  border: 1px solid rgba(255,255,255,0.08) !important;
  box-shadow: 0 12px 26px rgba(0,0,0,0.38) !important;
}

.category_btn:hover,
.category_btn button:hover {
  background: linear-gradient(135deg, #ff3038, #b9151b) !important;
}

#open_pdf_btn,
#open_pdf_btn button {
  background: linear-gradient(135deg, #ff262d, #c80f15) !important;
  box-shadow: 0 16px 34px rgba(0,0,0,0.38) !important;
}

#result_selector_radio .wrap,
#result_selector_radio .block,
#result_selector_radio fieldset,
.results_wrap,
.side_wrap {
  border-color: rgba(255,255,255,0.10) !important;
  background:
    linear-gradient(180deg, rgba(12,14,20,0.94), rgba(5,6,10,0.94)) !important;
  box-shadow:
    0 20px 48px rgba(0,0,0,0.48),
    inset 0 1px 0 rgba(255,255,255,0.055) !important;
}

.card {
  border-color: rgba(255,255,255,0.10);
  background:
    linear-gradient(180deg, rgba(27,31,43,0.84), rgba(14,17,24,0.86));
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}

.featured_cover,
.promo_stage,
.pdf_viewer_wrap {
  border-color: rgba(255,255,255,0.10);
  background: #050505;
}

@media (max-width: 767px) {
  #app_shell {
    max-width: 430px !important;
    padding: 10px !important;
  }

  .title_wrap {
    min-height: 190px;
    padding: 28px 14px 26px 14px;
    text-align: left;
  }

  .vault_logo_backdrop {
    inset: -25px -130px -35px -130px;
    background-size: 780px auto;
    background-position: center center;
    opacity: 0.52;
  }

  .title_eyebrow {
    font-size: 9px;
    letter-spacing: 0.34em;
    margin-bottom: 10px;
  }

  .title_main {
    font-size: 34px;
    line-height: 1;
  }

  .title_tag {
    font-size: 12px;
    line-height: 1.45;
    max-width: 310px;
  }

  #query_box textarea {
  min-height: 56px !important;
  height: 56px !important;
  max-height: 84px !important;
  font-size: 16px !important;
  padding: 10px 12px !important;
}
}

/* =========================================================
   MOBILE ZOOM / CENTERING FIX
   ========================================================= */

html,
body,
.gradio-container {
  max-width: 100vw !important;
  overflow-x: hidden !important;
  touch-action: pan-y !important;
}

#app_shell {
  width: 100% !important;
  max-width: 430px !important;
  margin-left: auto !important;
  margin-right: auto !important;
  overflow-x: hidden !important;
}

/* Center the hero words */
.title_wrap {
  text-align: center !important;
}

.title_content {
  margin-left: auto !important;
  margin-right: auto !important;
  text-align: center !important;
}

.title_eyebrow,
.title_main,
.title_tag {
  text-align: center !important;
  margin-left: auto !important;
  margin-right: auto !important;
}

/* iPhone Safari zoom prevention: inputs must be 16px or larger */
#query_box textarea,
#query_box input,
textarea,
input,
select {
  font-size: 16px !important;
}

/* Keep the query box from forcing page width wider */
#query_box,
#query_box *,
#result_selector_radio,
#result_selector_radio *,
.category_btn,
.category_btn button,
#browse_btn,
#browse_btn button,
#open_pdf_btn,
#open_pdf_btn button {
  max-width: 100% !important;
}

/* Stop the red category buttons from stretching beyond the centered app width */
#category_button_stack,
#category_button_row {
  width: 100% !important;
  max-width: 100% !important;
  overflow-x: hidden !important;
}

@media (max-width: 767px) {
  #app_shell {
    max-width: 430px !important;
    padding-left: 14px !important;
    padding-right: 14px !important;
  }

  .title_wrap {
    text-align: center !important;
  }

  .title_content {
    max-width: 100% !important;
    text-align: center !important;
  }

  .title_main {
    text-align: center !important;
    font-size: 32px !important;
    line-height: 1.02 !important;
  }

  .title_tag {
    text-align: center !important;
    max-width: 340px !important;
    font-size: 13px !important;
    line-height: 1.45 !important;
  }

  .title_eyebrow {
    text-align: center !important;
  }

  #query_box textarea {
    font-size: 16px !important;
    min-height: 56px !important;
    height: 56px !important;
    max-height: 84px !important;
    padding: 10px 12px !important;
  }

  #category_button_row {
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: 8px !important;
  }

  .category_btn,
  .category_btn button {
    width: 100% !important;
    min-width: 0 !important;
  }

  #browse_btn,
  #browse_btn button,
  #open_pdf_btn,
  #open_pdf_btn button {
    width: 100% !important;
    max-width: 100% !important;
  }
}

/* =========================================================
   DESKTOP WIDTH REFINEMENT ONLY
   ========================================================= */

@media (min-width: 1024px) {
  #app_shell {
    max-width: 720px !important;
    width: 720px !important;
    margin-left: auto !important;
    margin-right: auto !important;
  }

  .title_wrap,
  .free_access_wrap,
  #query_box,
  #browse_btn,
  #category_button_stack,
  #result_selector_radio,
  #open_pdf_btn,
  .featured_wrap,
  .side_wrap,
  .promo_shell,
  .results_wrap {
    width: 100% !important;
    max-width: 100% !important;
  }
}

/* Hide Gradio footer / settings */
footer {
  display: none !important;
}

.gradio-container footer {
  display: none !important;
}

#footer {
  display: none !important;
}

a[href*="gradio.app"] {
  display: none !important;
}

/* ---- the door home + the share row -------------------------------------- */
/* Sits under the title plate, above Free Public Access. Wraps on a phone so a
   narrow screen never pushes a button off the edge. */
.vault_share_row{
  display:flex; flex-wrap:wrap; gap:8px;
  align-items:center; justify-content:center;
  margin:14px auto 6px auto; max-width:920px;
}
.vault_home_btn, .vault_share_btn{
  display:inline-flex; align-items:center; justify-content:center;
  padding:9px 16px; border-radius:999px;
  font-weight:800; font-size:12px; letter-spacing:.04em;
  text-decoration:none !important; white-space:nowrap;
  transition:background .15s ease, border-color .15s ease;
}
.vault_home_btn{
  background:linear-gradient(180deg,#e11d2f,#a30d1c);
  border:1px solid rgba(255,255,255,.22); color:#fff !important;
}
.vault_home_btn:hover{ background:linear-gradient(180deg,#f2263a,#b81020); }
.vault_share_btn{
  background:rgba(255,255,255,.07);
  border:1px solid rgba(255,255,255,.18); color:#f2f2f2 !important;
}
.vault_share_btn:hover{ background:rgba(255,255,255,.14); border-color:rgba(255,255,255,.34); }
"""

# =========================================================
# INITIAL UI STATE
# =========================================================

initial_daily_html = refresh_jewel()
initial_featured_html = build_featured_shelf_html()
promo_state = make_promo_state()
initial_promo_html, promo_state = rotate_promo(promo_state)

# =========================================================
# GRADIO APP
# =========================================================

with gr.Blocks(css=CUSTOM_CSS, title="WeGotUsTV Library Vault") as vault_app:
    promo_state_store = gr.State(promo_state)

    selected_pdf_store = gr.State({
        "title": "",
        "url": "",
    })

    with gr.Column(elem_id="app_shell"):
        gr.HTML("""
        <div class="title_wrap">
          <div class="vault_logo_backdrop"></div>
          <div class="title_content">
            <div class="title_eyebrow">WE GOT US TV</div>
            <div class="title_main">WeGotUsTV Library Vault</div>
            <div class="title_tag">
              Unveil the hidden architecture of over 2,800 PDF books from the 15th–21st century.<br>
              The #1 library index full of forbidden books and buried transmissions. Search freely.
            </div>
          </div>
        </div>
        """)


        # =========================================================
        # THE WAY HOME, AND THE WAY OUT TO EVERYWHERE ELSE
        #
        # Esa: "we need to make sure they have a share button on it, and it has
        # a button that can link back to the WeGotUsTV platform."
        #
        # The vault sits on its own domain (vault.urbaninteractiveadventures.com)
        # and, until now, had no door back to the platform it belongs to and no
        # way for a visitor to pass it on. Somebody who found the vault could not
        # discover WeGotUsTV from it, and could not tell anyone about it without
        # copying the address bar.
        #
        # Plain anchor tags on purpose — Gradio owns this page's event loop, and
        # a share link needs no Python round trip. `target="_blank"` with
        # `rel="noopener"` so the vault is never left behind in the tab.
        # =========================================================
        gr.HTML("""
        <div class="vault_share_row">
          <a class="vault_home_btn" href="https://www.wegotustv.com" target="_blank" rel="noopener">← WeGotUsTV</a>
          <a class="vault_share_btn" target="_blank" rel="noopener"
             href="https://twitter.com/intent/tweet?text=The%20WeGotUsTV%20Library%20Vault%20%E2%80%94%20over%202%2C800%20PDF%20books%2C%2015th%E2%80%9321st%20century%2C%20free%20to%20search.%20%F0%9F%94%B4&url=https%3A%2F%2Fvault.urbaninteractiveadventures.com">𝕏 Post</a>
          <a class="vault_share_btn" target="_blank" rel="noopener"
             href="https://www.facebook.com/sharer/sharer.php?u=https%3A%2F%2Fvault.urbaninteractiveadventures.com">f Facebook</a>
          <a class="vault_share_btn" target="_blank" rel="noopener"
             href="https://t.me/share/url?url=https%3A%2F%2Fvault.urbaninteractiveadventures.com&text=The%20WeGotUsTV%20Library%20Vault%20%E2%80%94%20free%20to%20search.">✈ Telegram</a>
          <a class="vault_share_btn" target="_blank" rel="noopener"
             href="https://wa.me/?text=The%20WeGotUsTV%20Library%20Vault%20%E2%80%94%20free%20to%20search.%20https%3A%2F%2Fvault.urbaninteractiveadventures.com">◎ WhatsApp</a>
        </div>
        """)

        gr.HTML("""
        <div class="free_access_wrap">
          <div class="free_access_title">Free Public Access</div>
          <div class="free_access_text">
            We Got Us, Knowledge is Power.
          </div>
        </div>
        """)

        query_box = gr.Textbox(
            lines=3,
            max_lines=6,
            label="Search The Matrix",
            placeholder=BROWSE_HINT,
            elem_id="query_box",
            show_label=False,
            container=True,
            interactive=True,
            value="",
            scale=1
        )

        browse_btn = gr.Button("Browse the Library Vault", elem_id="browse_btn")

        gr.HTML('<div class="category_section_label">Explore by Category</div>')

        category_btns = []

        with gr.Column(elem_id="category_button_stack"):
            for row_cats in [TOP_CATEGORIES[:4], TOP_CATEGORIES[4:8]]:
                with gr.Row(elem_id="category_button_row", equal_height=True):
                    for cat in row_cats:
                        btn = gr.Button(cat, elem_classes=["category_btn"])
                        category_btns.append((btn, cat))

        result_selector = gr.Radio(
            choices=[],
            value=None,
            label="Storage Vault",
            interactive=True,
            elem_id="result_selector_radio"
        )

        open_pdf_btn = gr.Button("Open Selected PDF", elem_id="open_pdf_btn")

        pdf_viewer_box = gr.HTML(value="")

        featured_shelf_box = gr.HTML(value=initial_featured_html)

        daily_jewel_box = gr.HTML(value=initial_daily_html)

        refresh_jewel_btn = gr.Button("Refresh Jewel", elem_id="refresh_btn")

        promo_box = gr.HTML(value=initial_promo_html)
        promo_timer = gr.Timer(value=8.0, active=True)

    browse_btn.click(
        fn=browse_matrix_vault,
        inputs=query_box,
        outputs=[result_selector]
    )

    query_box.submit(
        fn=browse_matrix_vault,
        inputs=query_box,
        outputs=[result_selector]
    )

    for btn, cat in category_btns:
        btn.click(
            fn=lambda c=cat: browse_matrix_category(c),
            inputs=None,
            outputs=[result_selector]
        )

    result_selector.change(
        fn=select_book_by_title,
        inputs=[result_selector],
        outputs=[selected_pdf_store]
    )

    open_pdf_btn.click(
        fn=open_selected_book,
        inputs=[selected_pdf_store],
        outputs=[pdf_viewer_box]
    )

    refresh_jewel_btn.click(
        fn=refresh_vault_panels,
        inputs=None,
        outputs=[daily_jewel_box, featured_shelf_box]
    )

    promo_timer.tick(
        fn=rotate_promo,
        inputs=[promo_state_store],
        outputs=[promo_box, promo_state_store]
    )

# =========================================================
# FASTAPI PRODUCTION SERVER
# =========================================================

vault_app.queue()

server = FastAPI(title="WeGotUsTV Library Vault")

server.mount(
    "/assets",
    StaticFiles(directory=str(DATA_DIR)),
    name="assets"
)

@server.get("/health")
async def healthcheck():
    return {"status": "ok"}

app = gr.mount_gradio_app(
    app=server,
    blocks=vault_app,
    path="/"
)