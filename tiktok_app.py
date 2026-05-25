"""
TikTok Slideshow Pipeline — Streamlit UI
Run: streamlit run tiktok_app.py

Tabs:
  1. Settings       — API keys, creator config
  2. Analyze URL    — paste TikTok link → download → Claude analysis → hooks
  3. Hook Library   — browse, add, manage all hooks
  4. Creator Photos — upload inspiration → Nano Banana face-swap → backgrounds
  5. Slide Generator— pick hook → Claude content → render PNGs → download
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import time
import zipfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv, set_key

load_dotenv()
import config

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TikTok Slides",
    page_icon="🎴",
    layout="wide",
)

# ── Paths ─────────────────────────────────────────────────────────────────────
ENV_FILE        = Path(".env")
INSPIRATION_DIR = Path("input/inspiration")
IMAGES_DIR      = Path(config.TIKTOK_INPUT_IMAGES_DIR)
SLIDES_DIR      = Path(config.TIKTOK_OUTPUT_DIR)
DB_PATH         = Path(config.TIKTOK_DB_PATH)
HOOKS_FILE      = Path(config.TIKTOK_HOOKS_FILE)
COLLAGES_DIR    = Path("assets/model_collages")
IMAGE_EXTS      = {".jpg", ".jpeg", ".png", ".webp"}

for _d in [INSPIRATION_DIR, IMAGES_DIR, SLIDES_DIR, DB_PATH.parent, HOOKS_FILE.parent]:
    Path(_d).mkdir(parents=True, exist_ok=True)

# ── DB init ───────────────────────────────────────────────────────────────────
from tiktok_downloader import init_db, download as dl_tiktok, get_post
from tiktok_analyzer import analyze as analyze_post
init_db()

# ── Session state ─────────────────────────────────────────────────────────────
_SS_DEFAULTS: dict = {
    "an_record":    None,   # downloaded post record
    "an_analysis":  None,   # Claude analysis dict
    "gen_content":  None,   # slide content JSON
    "gen_paths":    [],     # rendered slide PNG paths
    "ph_generated": [],     # generated creator photo paths
}
for _k, _v in _SS_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v
ss = st.session_state


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _api_key(name: str) -> str:
    return os.environ.get(name, "")


def _save_key(name: str, value: str) -> None:
    value = value.strip()
    os.environ[name] = value
    ENV_FILE.touch()
    set_key(str(ENV_FILE), name, value)


def _badge(name: str) -> str:
    return "✅  set" if _api_key(name) else "❌  missing"


def _list_images(d: Path) -> list[Path]:
    if not d.exists():
        return []
    return sorted(f for f in d.iterdir() if f.suffix.lower() in IMAGE_EXTS)


def _zip_slides(paths: list[Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, p in enumerate(paths):
            if p.exists():
                zf.write(p, f"slide_{i + 1:02d}.png")
    return buf.getvalue()


def _slide_grid(paths: list[str | Path], n_cols: int = 4, label: str = "Slide") -> None:
    paths = [Path(p) for p in paths if Path(p).exists()]
    if not paths:
        return
    rows = [paths[i:i + n_cols] for i in range(0, len(paths), n_cols)]
    for row in rows:
        cols = st.columns(len(row))
        for col, p in zip(cols, row):
            idx = paths.index(p) + 1
            col.image(str(p), use_container_width=True, caption=f"{label} {idx}")


def _image_grid(paths: list[Path], n_cols: int = 5) -> None:
    if not paths:
        return
    rows = [paths[i:i + n_cols] for i in range(0, len(paths), n_cols)]
    for row in rows:
        cols = st.columns(len(row))
        for col, p in zip(cols, row):
            col.image(str(p), use_container_width=True, caption=p.name[:20])


def _all_hooks() -> list[str]:
    hooks: list[str] = []
    if HOOKS_FILE.exists():
        for line in HOOKS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in hooks:
                hooks.append(line)
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            for (v_json,) in conn.execute("SELECT variations FROM hooks").fetchall():
                for v in json.loads(v_json or "[]"):
                    v = v.strip()
                    if v and v not in hooks:
                        hooks.append(v)
            conn.close()
        except Exception:
            pass
    return hooks


def _append_hooks(hooks: list[str], source: str = "") -> int:
    HOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _all_hooks()
    added = 0
    with open(HOOKS_FILE, "a", encoding="utf-8") as f:
        if source:
            f.write(f"\n# {source}\n")
        for h in hooks:
            h = h.strip()
            if h and h not in existing:
                f.write(h + "\n")
                existing.append(h)
                added += 1
    return added


def _needs_key(*names: str) -> bool:
    missing = [n for n in names if not _api_key(n)]
    if missing:
        st.warning(
            f"⚠️ Missing API key(s): **{', '.join(missing)}** — add them in the ⚙️ Settings tab."
        )
        return True
    return False


def _resolve_collage(creator_key: str) -> Path | None:
    name = creator_key.split()[0]
    for ext in [".png", ".jpg", ".jpeg"]:
        p = COLLAGES_DIR / f"{name}{ext}"
        if p.exists():
            return p
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Layout
# ══════════════════════════════════════════════════════════════════════════════

st.title("🎴 TikTok Slideshow Pipeline")
st.caption("Personal finance · Girly aesthetic · Built for women 20–40")

T_SETTINGS, T_ANALYZE, T_HOOKS, T_PHOTOS, T_SLIDES = st.tabs([
    "⚙️  Settings",
    "🔍  Analyze URL",
    "📚  Hook Library",
    "📸  Creator Photos",
    "🎴  Slide Generator",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

with T_SETTINGS:
    st.header("⚙️ Settings")
    st.caption("API keys are saved to `.env` and take effect immediately.")
    st.divider()

    # ── API Keys ──────────────────────────────────────────────────────────────
    st.subheader("API Keys")

    KEY_DEFS = [
        ("ANTHROPIC_API_KEY",  "Anthropic API Key",
         "Claude Opus 4.7 — hook analysis, content generation"),
        ("WAVESPEED_API_KEY",  "WaveSpeed API Key",
         "Nano Banana 2 — creator photo face-swap"),
    ]

    for env_name, label, desc in KEY_DEFS:
        c1, c2, c3 = st.columns([4, 1, 1])
        with c1:
            new_val = st.text_input(
                label, value=_api_key(env_name),
                type="password", help=desc, key=f"s_key_{env_name}",
            )
        with c2:
            st.write("")
            if st.button("💾 Save", key=f"s_save_{env_name}", use_container_width=True):
                _save_key(env_name, new_val)
                st.success("Saved!")
                st.rerun()
        with c3:
            st.write("")
            st.caption(_badge(env_name))

    st.divider()

    # ── Creator overview ──────────────────────────────────────────────────────
    st.subheader("Creators")
    for name, c in config.TIKTOK_CREATORS.items():
        collage = _resolve_collage(name)
        cc1, cc2 = st.columns([1, 4])
        with cc1:
            if collage:
                st.image(str(collage), width=120)
            else:
                st.caption("_no collage_")
        with cc2:
            st.markdown(f"**{name}**")
            st.caption(
                f"Age {c['age']}  ·  {c['handle']}  \n"
                f"{c['tone']}"
            )
        st.markdown("")

    st.divider()

    # ── Font ──────────────────────────────────────────────────────────────────
    st.subheader("Font")
    font_path = Path(config.TIKTOK_FONT_PATH)
    if font_path.exists():
        st.success(f"✅ `{font_path}` found — slides will use Montserrat Black.")
    else:
        st.info(
            "**Montserrat Black** not found. Slides fall back to a system font.\n\n"
            "Download from [fonts.google.com/specimen/Montserrat](https://fonts.google.com/specimen/Montserrat) "
            "→ extract **Montserrat-Black.ttf** → place in `assets/fonts/`."
        )
        uploaded_font = st.file_uploader(
            "Or upload Montserrat-Black.ttf here",
            type=["ttf"], key="s_font_upload",
        )
        if uploaded_font:
            font_path.parent.mkdir(parents=True, exist_ok=True)
            font_path.write_bytes(uploaded_font.getvalue())
            st.success(f"✅ Font saved to `{font_path}`")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — ANALYZE URL
# ══════════════════════════════════════════════════════════════════════════════

with T_ANALYZE:
    st.header("🔍 Analyze TikTok URL")
    st.caption(
        "Paste any TikTok slideshow link → yt-dlp downloads the slides → "
        "Claude Opus 4.7 extracts the hook structure + Pinterest queries."
    )
    st.divider()

    if _needs_key("ANTHROPIC_API_KEY"):
        st.stop()

    an_url = st.text_input(
        "TikTok URL",
        placeholder="https://www.tiktok.com/@user/photo/1234567890",
        key="an_url_input",
    )

    # ── Cookie auth ───────────────────────────────────────────────────────────
    from tiktok_downloader import COOKIES_FILE
    with st.expander(
        "🍪  Cookie auth " + ("✅  active" if COOKIES_FILE.exists() else "⚠️  not set — public posts only"),
        expanded=not COOKIES_FILE.exists(),
    ):
        st.caption(
            "TikTok blocks most downloads without a logged-in session. "
            "Export your cookies with the **[Get cookies.txt LOCALLY]"
            "(https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** "
            "Chrome extension → save as `cookies.txt` → upload below."
        )
        cookie_file = st.file_uploader(
            "Upload cookies.txt", type=["txt"], key="an_cookies",
        )
        if cookie_file:
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_bytes(cookie_file.getvalue())
            st.success(f"✅ Cookies saved — all future downloads will use them.")
            st.rerun()
        if COOKIES_FILE.exists():
            col1, col2 = st.columns([3, 1])
            col1.caption(f"Active cookie file: `{COOKIES_FILE}` "
                         f"({COOKIES_FILE.stat().st_size // 1024} KB)")
            if col2.button("🗑️ Remove", key="an_del_cookies"):
                COOKIES_FILE.unlink()
                st.rerun()

    st.divider()

    if an_url and st.button("🚀 Download & Analyze", type="primary",
                             use_container_width=True, key="an_run"):
        ss.an_record = None
        ss.an_analysis = None

        with st.status("⬇️  Downloading slideshow…", expanded=True) as s_dl:
            try:
                rec = dl_tiktok(an_url)
                # rec is None or has _error key on failure
                if not rec or rec.get("_error"):
                    err = (rec or {}).get("_error", "Unknown error")
                    s_dl.update(label="❌ Download failed", state="error")
                    st.error(f"**yt-dlp error:** {err}")
                    if not COOKIES_FILE.exists():
                        st.info(
                            "💡 Most TikTok videos require you to be logged in. "
                            "Upload your **cookies.txt** in the expander above and try again."
                        )
                    st.stop()
                ss.an_record = rec
                paths = [Path(p) for p in rec.get("image_paths", []) if Path(p).exists()]
                s_dl.update(
                    label=f"✅ {len(paths)} slide(s) downloaded from @{rec.get('author', '?')}",
                    state="complete",
                )
            except Exception as e:
                s_dl.update(label=f"❌ {e}", state="error")
                st.stop()

        with st.status("🤖  Analyzing with Claude Opus 4.7…", expanded=True) as s_an:
            try:
                analysis = analyze_post(ss.an_record["post_id"])
                if not analysis:
                    s_an.update(label="❌ Analysis failed", state="error")
                    st.stop()
                ss.an_analysis = analysis
                s_an.update(
                    label=f"✅ Hook extracted — type: {analysis.get('hook_type', '?')}",
                    state="complete",
                )
            except Exception as e:
                s_an.update(label=f"❌ {e}", state="error")
                st.stop()

        st.rerun()

    # ── Results ───────────────────────────────────────────────────────────────
    if ss.an_record and ss.an_analysis:
        rec      = ss.an_record
        analysis = ss.an_analysis

        st.divider()

        # Downloaded slides preview
        paths = [Path(p) for p in rec.get("image_paths", []) if Path(p).exists()]
        if paths:
            st.subheader(f"Downloaded — {len(paths)} slide(s)")
            st.caption(f"@{rec.get('author', '?')}  ·  {rec.get('caption', '')[:120]}")
            _image_grid(paths, n_cols=min(len(paths), 6))
            st.divider()

        # Hook analysis
        r1, r2 = st.columns(2)
        with r1:
            st.subheader("Hook Analysis")
            st.markdown(f"**Hook**")
            st.info(analysis.get("hook", ""))

            st.markdown(f"**Type:** `{analysis.get('hook_type', '')}`")
            st.markdown(f"**Why it works**")
            st.write(analysis.get("why_it_works", ""))
            st.markdown(f"**Structure**")
            st.code(analysis.get("hook_structure", ""), language=None)

        with r2:
            st.subheader("5 Hook Variations")
            for i, v in enumerate(analysis.get("variations", []), 1):
                st.markdown(f"**{i}.** {v}")

            st.divider()
            st.subheader("Pinterest Queries")
            for q in analysis.get("pinterest_queries", []):
                st.markdown(f"• `{q}`")

        st.divider()

        # Add to library
        variations = analysis.get("variations", [])
        added_count = st.session_state.get("an_last_added", 0)
        if variations:
            if st.button(
                f"📚 Add {len(variations)} variations to Hook Library",
                type="primary", use_container_width=True, key="an_add_hooks",
            ):
                n = _append_hooks(variations, source=rec.get("url", ""))
                st.success(f"✅ Added {n} new hook(s) to the library.")
                st.session_state["an_last_added"] = n


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — HOOK LIBRARY
# ══════════════════════════════════════════════════════════════════════════════

with T_HOOKS:
    st.header("📚 Hook Library")
    st.caption(f"All hooks from `{HOOKS_FILE}` and the SQLite DB. "
               "Used by the Slide Generator.")
    st.divider()

    # ── Add custom hooks ──────────────────────────────────────────────────────
    st.subheader("Add Hooks Manually")
    custom_raw = st.text_area(
        "One hook per line",
        placeholder="Just in case your parents didn't teach you about money\n"
                    "6 toxic money habits you NEED to break\n"
                    "Rules rich people don't talk about",
        height=150, key="hl_custom",
    )
    if st.button("➕ Add to Library", key="hl_add", use_container_width=True):
        lines = [l.strip() for l in custom_raw.splitlines() if l.strip()]
        if lines:
            n = _append_hooks(lines, source="manual")
            st.success(f"✅ Added {n} new hook(s).")
            st.rerun()
        else:
            st.warning("Nothing to add — type at least one hook above.")

    st.divider()

    # ── Browse library ────────────────────────────────────────────────────────
    hooks = _all_hooks()
    st.subheader(f"Library — {len(hooks)} hook(s)")

    if not hooks:
        st.info("No hooks yet. Analyze a TikTok URL or add hooks manually above.")
    else:
        hl_search = st.text_input("🔎 Filter", placeholder="type to search…", key="hl_search")
        filtered  = [h for h in hooks if hl_search.lower() in h.lower()] if hl_search else hooks
        st.caption(f"Showing {len(filtered)} of {len(hooks)}")

        for i, hook in enumerate(filtered):
            hc1, hc2 = st.columns([6, 1])
            with hc1:
                st.markdown(f"**{i+1}.** {hook}")
            with hc2:
                if st.button("🗑️", key=f"hl_del_{i}", help="Remove from file"):
                    # Remove from hooks file (DB entries stay)
                    if HOOKS_FILE.exists():
                        lines = HOOKS_FILE.read_text(encoding="utf-8").splitlines()
                        lines = [l for l in lines if l.strip() != hook]
                        HOOKS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CREATOR PHOTOS
# ══════════════════════════════════════════════════════════════════════════════

with T_PHOTOS:
    st.header("📸 Creator Photos")
    st.caption(
        "Upload lifestyle/selfie inspiration images → Nano Banana swaps in the "
        "creator's face → photos land in `input/images/` ready for slides."
    )
    st.divider()

    if _needs_key("ANTHROPIC_API_KEY", "WAVESPEED_API_KEY"):
        st.stop()

    ph_creator = st.selectbox(
        "Creator", list(config.TIKTOK_CREATORS.keys()), key="ph_creator",
    )
    ph_collage = _resolve_collage(ph_creator)
    if not ph_collage:
        st.error(
            f"No identity collage found for **{ph_creator}**. "
            "Add `{ph_creator.split()[0]}.png` to `assets/model_collages/`."
        )
        st.stop()

    c_col, c_info = st.columns([1, 3])
    with c_col:
        st.image(str(ph_collage), width=120, caption="Identity collage")
    with c_info:
        st.markdown(f"**{ph_creator}**")
        c = config.TIKTOK_CREATORS[ph_creator]
        st.caption(f"Age {c['age']}  ·  {c['handle']}")
        st.caption(c["tone"])

    st.divider()

    # ── Inspiration images ────────────────────────────────────────────────────
    st.subheader("1 · Inspiration Images")
    st.caption(
        "Lifestyle selfies: café, mirror, outdoor, bedroom — NOT finance-themed. "
        "These define the scene; Fiona/Lisa's face replaces whoever is in the photo."
    )

    ph_uploaded = st.file_uploader(
        "Upload inspiration images",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="ph_uploads",
    )
    if ph_uploaded and st.button("💾 Save to inspiration folder", key="ph_save_inspo"):
        INSPIRATION_DIR.mkdir(parents=True, exist_ok=True)
        for f in ph_uploaded:
            dest = INSPIRATION_DIR / f.name
            dest.write_bytes(f.getvalue())
        st.success(f"Saved {len(ph_uploaded)} image(s) to `{INSPIRATION_DIR}/`")
        st.rerun()

    inspo_images = _list_images(INSPIRATION_DIR)
    if inspo_images:
        st.caption(f"✅ {len(inspo_images)} inspiration image(s) ready:")
        _image_grid(inspo_images, n_cols=min(len(inspo_images), 5))
    else:
        st.info("No inspiration images yet. Upload above or drop files into `input/inspiration/`.")

    st.divider()

    # ── Generate ──────────────────────────────────────────────────────────────
    st.subheader("2 · Generate Creator Photos")
    if not inspo_images:
        st.info("Add inspiration images above first.")
    else:
        if st.button(
            f"🎨 Generate {len(inspo_images)} photo(s) with Nano Banana",
            type="primary", use_container_width=True, key="ph_generate",
        ):
            from generate_images import (
                generate_batch_parallel, fill_prompt_with_claude, _load_master_prompt
            )

            master = _load_master_prompt()
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)

            # Build status boxes
            status_boxes = {i: st.empty() for i in range(len(inspo_images))}
            for i, p in enumerate(inspo_images):
                status_boxes[i].info(f"Image {i+1} ({p.name}) — queued")

            # Generate prompts
            prompts = []
            with st.status(f"🤖 Filling {len(inspo_images)} prompts with Claude…") as s_pr:
                for i, ref in enumerate(inspo_images):
                    status_boxes[i].info(f"Image {i+1} — analysing reference…")
                    try:
                        p = fill_prompt_with_claude(ph_collage, ref, master)
                        prompts.append(p)
                        status_boxes[i].success(f"Image {i+1} — prompt ready ✓")
                    except Exception as e:
                        prompts.append("")
                        status_boxes[i].error(f"Image {i+1} — prompt failed: {e}")
                s_pr.update(label="✅ Prompts ready", state="complete")

            # Filter out failed prompts
            valid = [(i, ref, p) for i, (ref, p) in enumerate(zip(inspo_images, prompts)) if p]
            if not valid:
                st.error("All prompt generations failed.")
                st.stop()

            out_paths = [IMAGES_DIR / f"photo_{i:02d}.jpg" for i, _, _ in valid]

            def _cb(idx, msg):
                b = status_boxes.get(idx)
                if b:
                    b.info(f"Image {idx + 1} — {msg}")

            with st.status(f"🍌 Generating {len(valid)} photo(s) via Nano Banana…") as s_gen:
                try:
                    results = generate_batch_parallel(
                        ref_paths=[ref for _, ref, _ in valid],
                        collage_path=ph_collage,
                        prompts=[p for _, _, p in valid],
                        api_key=_api_key("WAVESPEED_API_KEY"),
                        max_concurrent=3,
                        max_retries=2,
                        status_cb=_cb,
                        indices=[i for i, _, _ in valid],
                        out_paths=out_paths,
                    )
                    ok = [r for r in results if r.status == "success"]
                    for r in ok:
                        if r.out_path:
                            status_boxes.get(r.idx, st.empty()).success(
                                f"Image {r.idx + 1} — ✅ {r.out_path.name}"
                            )
                    ss.ph_generated = [str(r.out_path) for r in ok if r.out_path]
                    s_gen.update(
                        label=f"✅ {len(ok)}/{len(valid)} photos generated → `{IMAGES_DIR}/`",
                        state="complete",
                    )
                except Exception as e:
                    s_gen.update(label=f"❌ {e}", state="error")
                    st.error(str(e))

    # ── Generated photos preview ──────────────────────────────────────────────
    existing_photos = _list_images(IMAGES_DIR)
    if existing_photos:
        st.divider()
        st.subheader(f"Photos in `input/images/`  —  {len(existing_photos)} file(s)")
        st.caption("These are used as slide backgrounds in the Slide Generator.")
        _image_grid(existing_photos, n_cols=min(len(existing_photos), 5))

        if st.button("🗑️ Clear all background images", key="ph_clear"):
            for f in existing_photos:
                f.unlink()
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — SLIDE GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

with T_SLIDES:
    st.header("🎴 Slide Generator")
    st.caption(
        "Pick a hook → Claude writes the slideshow content → renders 7 PNG slides → download."
    )
    st.divider()

    if _needs_key("ANTHROPIC_API_KEY"):
        st.stop()

    # ── Creator + hook ────────────────────────────────────────────────────────
    sg_creator = st.selectbox(
        "Creator", list(config.TIKTOK_CREATORS.keys()), key="sg_creator",
    )
    c_info = config.TIKTOK_CREATORS[sg_creator]
    st.caption(f"Age {c_info['age']}  ·  {c_info['handle']}  ·  {c_info['tone'][:70]}…")

    st.divider()
    st.subheader("1 · Choose Hook")

    sg_hook_mode = st.radio(
        "Source", ["📚 From library", "✏️ Type custom"],
        horizontal=True, key="sg_hook_mode",
    )

    hook_text = ""
    library_hooks = _all_hooks()

    if sg_hook_mode == "📚 From library":
        if not library_hooks:
            st.info("Hook library is empty. Analyze a URL or add hooks in the Hook Library tab.")
        else:
            selected_hook = st.selectbox(
                "Select hook", library_hooks, key="sg_hook_sel",
            )
            hook_text = selected_hook
    else:
        hook_text = st.text_input(
            "Your hook",
            placeholder="just in case your parents never taught you about money",
            key="sg_hook_custom",
        )

    if hook_text:
        st.success(f"**Selected:** {hook_text}")

    st.divider()

    # ── Generate content ──────────────────────────────────────────────────────
    st.subheader("2 · Generate Slide Content")

    if not hook_text:
        st.info("Choose or type a hook above.")
    elif st.button("🤖 Generate Content (Claude Opus 4.7)", type="primary",
                   use_container_width=True, key="sg_gen_content"):
        from tiktok_content_generator import generate_slide_content
        with st.spinner("Claude is writing your slideshow…"):
            try:
                content = generate_slide_content(hook_text, sg_creator)
                ss.gen_content = content
                ss.gen_paths   = []   # reset old slides
            except Exception as e:
                st.error(f"Content generation failed: {e}")

    # ── Content editor ────────────────────────────────────────────────────────
    if ss.gen_content:
        content = ss.gen_content
        st.divider()
        st.subheader("3 · Review & Edit Content")
        st.caption("Tweak any text before generating the PNG slides.")

        # Hook (slide 1)
        edited_hook = st.text_input(
            "Hook (Slide 1)", value=content["hook"], key="sg_e_hook",
        )
        content["hook"] = edited_hook

        # Content slides
        for i, slide in enumerate(content["slides"]):
            with st.expander(f"Slide {i+2}  —  #{slide['number']}  {slide['title'].upper()}", expanded=False):
                ec1, ec2 = st.columns(2)
                with ec1:
                    new_title = st.text_input(
                        "Title", value=slide["title"], key=f"sg_e_title_{i}",
                    )
                    slide["title"] = new_title
                with ec2:
                    new_body = st.text_area(
                        "Body", value=slide["body"], height=80, key=f"sg_e_body_{i}",
                    )
                    slide["body"] = new_body

        # CTA (last slide)
        edited_cta = st.text_input(
            "CTA (Last Slide)", value=content["cta"], key="sg_e_cta",
        )
        content["cta"] = edited_cta

        st.divider()

        # ── Background images ─────────────────────────────────────────────────
        st.subheader("4 · Background Images")
        bg_images = _list_images(IMAGES_DIR)
        if bg_images:
            st.caption(
                f"✅ {len(bg_images)} background image(s) in `input/images/` — "
                "these will be used as slide backgrounds."
            )
            _image_grid(bg_images, n_cols=min(len(bg_images), 6))
        else:
            st.warning(
                "No background images yet. "
                "Go to **📸 Creator Photos** to generate Fiona/Lisa photos, "
                "or upload images directly below."
            )
            manual_bgs = st.file_uploader(
                "Upload background images",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                key="sg_bg_upload",
            )
            if manual_bgs and st.button("💾 Save as backgrounds", key="sg_save_bgs"):
                IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                for f in manual_bgs:
                    (IMAGES_DIR / f.name).write_bytes(f.getvalue())
                st.rerun()

        st.divider()

        # ── Generate slides ───────────────────────────────────────────────────
        st.subheader("5 · Generate PNG Slides")

        bg_images = _list_images(IMAGES_DIR)
        if not bg_images:
            st.info("Add background images above first.")
        elif st.button(
            "🎴 Generate Slides", type="primary",
            use_container_width=True, key="sg_gen_slides",
        ):
            from generate_slides import generate_slideshow
            import time as _time
            ts = _time.strftime("%Y%m%d_%H%M%S")
            safe = hook_text[:35].replace(" ", "_").replace("/", "-")
            out_dir = SLIDES_DIR / f"{ts}_{safe}"

            with st.spinner("Rendering slides…"):
                try:
                    paths = generate_slideshow(content, sg_creator, str(out_dir))
                    ss.gen_paths = paths
                    # Save content JSON
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (Path(out_dir) / "content.json").write_text(
                        json.dumps({"creator": sg_creator, "content": content}, indent=2)
                    )
                except Exception as e:
                    st.error(f"Slide generation failed: {e}")

        # ── Preview + download ────────────────────────────────────────────────
        if ss.gen_paths:
            existing = [Path(p) for p in ss.gen_paths if Path(p).exists()]
            if existing:
                st.divider()
                st.subheader(f"✅ {len(existing)} Slides Generated")
                _slide_grid(existing, n_cols=4)

                st.download_button(
                    label=f"⬇️ Download all {len(existing)} slides (.zip)",
                    data=_zip_slides(existing),
                    file_name=f"tiktok_slides_{sg_creator.split()[0].lower()}.zip",
                    mime="application/zip",
                    use_container_width=True,
                    type="primary",
                    key="sg_dl_zip",
                )
