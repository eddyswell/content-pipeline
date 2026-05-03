"""
Content Pipeline — Streamlit UI
Tab-based production tool.

Tabs:
  1. Creators       — define identity, voice, collage
  2. Scene Builder  — Nano Banana base-image generation
  3. Scene Variations — Seedream variation generation + review
  4. Reel Generator — HeyGen video pipeline
  5. History        — browse scenes, packs, reels
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import ssl
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import aiohttp
import aiofiles
import certifi
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import config
import generate_voices
import generate_images
import generate_variations
import animate_batch
import reel_pipeline

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Content Pipeline", page_icon="🎬", layout="wide")

HEYGEN_BASE   = "https://api.heygen.com"
HEYGEN_UPLOAD = "https://upload.heygen.com"
N_VARS        = 2
MODELS_DIR    = Path("models")
SCENES_DIR    = Path("scenes")


# ══════════════════════════════════════════════════════════════════════════════
# File-system helpers
# ══════════════════════════════════════════════════════════════════════════════

def _slug(name: str) -> str:
    """'Fiona (US)' → 'fiona_us'"""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def load_creators() -> dict[str, dict]:
    """Merge legacy config.py creators + custom models/ creators."""
    creators: dict[str, dict] = {}
    for name, data in config.CREATORS.items():
        creators[name] = {"source": "legacy", **data}
    if MODELS_DIR.exists():
        for d in sorted(MODELS_DIR.iterdir()):
            if not (d.is_dir() and (d / "config.json").exists()):
                continue
            try:
                data = json.loads((d / "config.json").read_text())
                name = data.get("name", d.name)
                creators[name] = {"source": "custom", **data}
            except Exception:
                pass
    return creators


def save_creator(
    name: str,
    collage_bytes: bytes,
    collage_ext: str,
    voice_id: str,
    description: str,
    voice_settings: dict,
) -> None:
    d = MODELS_DIR / _slug(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"collage{collage_ext}").write_bytes(collage_bytes)
    (d / "config.json").write_text(json.dumps({
        "name": name,
        "voice_id": voice_id,
        "description": description,
        "voice_settings": voice_settings,
    }, indent=2))


def resolve_collage(creator_name: str) -> Path | None:
    """Check models/{slug}/ first, then legacy assets/model_collages/."""
    slug = _slug(creator_name)
    for ext in [".png", ".jpg", ".jpeg"]:
        p = MODELS_DIR / slug / f"collage{ext}"
        if p.exists():
            return p
    short = creator_name.split()[0]
    for ext in [".png", ".jpg", ".jpeg"]:
        p = Path("assets/model_collages") / f"{short}{ext}"
        if p.exists():
            return p
    return None


def scene_dir(creator_name: str, scene_name: str) -> Path:
    return SCENES_DIR / _slug(creator_name) / scene_name


def get_scenes(creator_name: str) -> list[str]:
    d = SCENES_DIR / _slug(creator_name)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and (p / "base.jpg").exists())


def get_base_image(creator_name: str, scene_name: str) -> Path | None:
    p = scene_dir(creator_name, scene_name) / "base.jpg"
    return p if p.exists() else None


def var_path(creator_name: str, scene_name: str, v_i: int) -> Path:
    return scene_dir(creator_name, scene_name) / "variations" / f"v_{v_i:02d}.jpg"


def get_variations(creator_name: str, scene_name: str) -> dict[int, Path]:
    return {
        i: p for i in range(N_VARS)
        if (p := var_path(creator_name, scene_name, i)).exists()
    }


def load_approved(creator_name: str, scene_name: str) -> list[int]:
    p = scene_dir(creator_name, scene_name) / "approved.json"
    try:
        return json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []


def save_approved(creator_name: str, scene_name: str, approved: list[int]) -> None:
    p = scene_dir(creator_name, scene_name) / "approved.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(approved))


def save_scene_meta(creator_name: str, scene_name: str, meta: dict) -> None:
    d = scene_dir(creator_name, scene_name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, indent=2, default=str))


def load_scene_meta(creator_name: str, scene_name: str) -> dict:
    p = scene_dir(creator_name, scene_name) / "meta.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# Generic UI helpers
# ══════════════════════════════════════════════════════════════════════════════

def run_step(fn, *args, **kwargs):
    import sys
    real = sys.exit
    sys.exit = lambda c=0: (_ for _ in ()).throw(RuntimeError(f"exit {c}")) if c else None
    try:
        fn(*args, **kwargs)
    finally:
        sys.exit = real


def make_zip(videos: list[Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for v in videos:
            zf.write(v, v.name)
    return buf.getvalue()


def zip_scene(scene_path: Path, approved_only: bool = False) -> bytes:
    """ZIP a scene directory.
    approved_only=False → base.jpg + all files in variations/ + all_variations/
    approved_only=True  → only the approved variation files
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if not approved_only:
            base = scene_path / "base.jpg"
            if base.exists():
                zf.write(base, "base.jpg")
            for sub in ("variations", "all_variations"):
                d = scene_path / sub
                if d.exists():
                    for p in sorted(d.glob("v_*.jpg")):
                        zf.write(p, f"{sub}/{p.name}")
        else:
            app_file = scene_path / "approved.json"
            if app_file.exists():
                try:
                    approved = json.loads(app_file.read_text())
                    for vi in approved:
                        p = scene_path / "variations" / f"v_{vi:02d}.jpg"
                        if p.exists():
                            zf.write(p, f"approved_V{vi + 1}.jpg")
                except Exception:
                    pass
    return buf.getvalue()


def update_voice_id(creator_name: str, creator_data: dict, new_voice_id: str) -> None:
    """Save an updated voice ID for any creator (creates a models/ entry for legacy ones)."""
    slug      = _slug(creator_name)
    model_dir = MODELS_DIR / slug
    model_dir.mkdir(parents=True, exist_ok=True)
    cfg_path  = model_dir / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
    else:
        # Bootstrap from legacy data
        cfg = {
            "name":           creator_name,
            "description":    creator_data.get("description", ""),
            "voice_settings": creator_data.get("voice_settings", {}),
        }
    cfg["voice_id"] = new_voice_id
    cfg_path.write_text(json.dumps(cfg, indent=2))


def save_uploads(files, tmp: Path | None = None) -> list[Path]:
    tmp = tmp or Path(tempfile.mkdtemp())
    paths = []
    for f in files:
        dest = tmp / f.name
        dest.write_bytes(f.getvalue())
        paths.append(dest)
    return paths


def img_grid(paths: list[Path], n_cols: int = 5, captions: list[str] | None = None):
    if not paths:
        return
    cols = st.columns(n_cols)
    for i, p in enumerate(paths):
        with cols[i % n_cols]:
            cap = (captions[i] if captions and i < len(captions) else Path(p).name[:18])
            st.image(str(p), width="stretch", caption=cap)


# ── HeyGen helpers ─────────────────────────────────────────────────────────────

def _ctype(path: Path) -> str:
    with open(path, "rb") as f:
        h = f.read(12)
    if h[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if h[:3]  == b"\xff\xd8\xff":     return "image/jpeg"
    if h[:4]  == b"RIFF" and h[8:12] == b"WEBP": return "image/webp"
    return "image/jpeg"


async def _hg_upload(session, path: Path, key: str) -> str:
    headers = {"X-Api-Key": key, "Content-Type": _ctype(path)}
    async with aiofiles.open(path, "rb") as f:
        data = await f.read()
    async with session.post(f"{HEYGEN_UPLOAD}/v1/asset", data=data, headers=headers) as r:
        body = await r.text()
        if not r.ok:
            raise RuntimeError(f"HeyGen upload {r.status}: {body[:300]}")
        return json.loads(body)["data"]["image_key"]


async def _hg_create(session, img_key: str, name: str, key: str) -> str:
    hdrs = {"X-Api-Key": key, "Content-Type": "application/json"}
    async with session.post(
        f"{HEYGEN_BASE}/v2/photo_avatar/avatar_group/create",
        json={"name": name, "image_key": img_key}, headers=hdrs,
    ) as r:
        body = await r.text()
        if not r.ok:
            raise RuntimeError(f"Talking photo {r.status}: {body[:300]}")
        return json.loads(body)["data"]["id"]


async def _hg_wait(session, av_id: str, key: str) -> bool:
    hdrs = {"X-Api-Key": key}
    for _ in range(24):
        await asyncio.sleep(10)
        async with session.get(
            f"{HEYGEN_BASE}/v2/photo_avatar/avatar_group/{av_id}", headers=hdrs,
        ) as r:
            if r.ok:
                s = json.loads(await r.text()).get("data", {}).get("status", "")
                if s == "completed": return True
                if s == "failed":    return False
    return False


async def _register_images(paths: list[Path], key: str, progress_cb) -> list[str]:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    ids: list[str] = []
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl_ctx)
    ) as s:
        for i, path in enumerate(paths, 1):
            total = len(paths)
            progress_cb(i, total, f"Uploading {i}/{total}…")
            ik = await _hg_upload(s, path, key)
            progress_cb(i, total, f"Registering {i}/{total}…")
            av = await _hg_create(s, ik, f"batch_{i:02d}", key)
            progress_cb(i, total, f"Waiting {i}/{total}…")
            await _hg_wait(s, av, key)
            ids.append(av)
    return ids


def run_video_pipeline(final_paths, active_hooks, creator_data, heygen_key, bar, start_pct):
    n = len(final_paths)

    reg = st.status(f"📸 Registering {n} images with HeyGen…", expanded=True)
    txt = reg.empty()

    def _rp(i, t, msg):
        txt.text(msg)
        bar.progress(start_pct + int((i / t) * (55 - start_pct)), text=msg)

    try:
        av_ids = asyncio.run(_register_images(final_paths, heygen_key, _rp))
        reg.update(label=f"✅ {n} images registered", state="complete")
        bar.progress(55, text="Generating voices…")
    except Exception as e:
        reg.update(label="❌ HeyGen registration failed", state="error")
        st.error(str(e)); st.stop()

    with st.status(f"🎙️ Generating {n} voice files…") as s:
        try:
            run_step(generate_voices.run)
            s.update(label=f"✅ {n} voices", state="complete")
            bar.progress(70, text="Generating videos…")
        except Exception as e:
            s.update(label="❌ Voice gen failed", state="error")
            st.error(str(e)); st.stop()

    with st.status(f"🎬 Generating {n} HeyGen videos…") as s:
        try:
            run_step(animate_batch.run, avatar_ids_override=av_ids)
            # Copy output/videos/video_*.mp4 → output/final/final_*.mp4
            final_dir = Path("output/final")
            final_dir.mkdir(parents=True, exist_ok=True)
            copied = 0
            for vf in sorted(Path("output/videos").glob("video_*.mp4")):
                num = vf.stem.split("_")[-1]
                dest = final_dir / f"final_{num}.mp4"
                shutil.copy2(str(vf), str(dest))
                copied += 1
            s.update(label=f"✅ {copied} video{'s' if copied != 1 else ''} ready", state="complete")
            bar.progress(100, text="Done!")
        except Exception as e:
            s.update(label="❌ Video gen failed", state="error")
            st.error(str(e)); st.stop()

    st.success(f"🎉 {n} reel{'s' if n > 1 else ''} ready!")


# ══════════════════════════════════════════════════════════════════════════════
# Session state
# ══════════════════════════════════════════════════════════════════════════════

_SS_DEFAULTS: dict = {
    # Tab 2: Scene Builder
    "sb_tmp_dir":    None,  # Path — temp dir for uploaded refs
    "sb_ref_paths":  [],    # list[Path]
    "sb_prompts":    {},    # {i: str}
    "sb_scene_names": {},   # {i: str} — persisted names, saved explicitly by user

    # Tab 3: Scene Variations
    "sv_creator":     "",
    "sv_scene":       "",
    "sv_var_prompts": {},   # {v_i: str}
    "sv_var_paths":   {},   # {v_i: Path}
    "sv_var_sel":     {},   # {v_i: bool}

    # Tab 4: Reel Generator
    "rg_last_scene": "",    # tracks scene changes to reset selection
    "rg_sel":        {},    # {i: bool} — which pack images are selected for reels

    # Tab 6: Instagram → AI Reel
    "rp_jobs": [],          # list of job dicts, one per URL
}

for _k, _v in _SS_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

ss = st.session_state

heygen_key    = os.getenv("HEYGEN_API_KEY", "")
wavespeed_key = os.getenv("WAVESPEED_API_KEY", "")
anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
mp_path       = Path("assets/master_prompt.txt")


# ══════════════════════════════════════════════════════════════════════════════
# Layout
# ══════════════════════════════════════════════════════════════════════════════

st.title("🎬 Content Pipeline")
st.caption("Creators → Scenes → Scene Packs → Reels")

T_CREATORS, T_BUILDER, T_VARS, T_REELS, T_HISTORY, T_REEL_PIPE = st.tabs([
    "👤 Creators",
    "🎨 Scene Builder",
    "✨ Scene Variations",
    "🎬 Reel Generator",
    "📋 History",
    "🎥 Instagram → AI Reel",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CREATORS
# ══════════════════════════════════════════════════════════════════════════════

with T_CREATORS:
    st.header("👤 Creators")
    st.caption("Define the identity, voice, and reference collage for each creator.")
    st.divider()

    creators = load_creators()
    col_nav, col_detail = st.columns([1, 2], gap="large")

    with col_nav:
        st.caption("Select creator")
        nav_options = list(creators.keys()) + ["➕ New Creator"]
        t1_sel = st.radio("creator_nav", nav_options,
                          label_visibility="collapsed", key="t1_nav")

    with col_detail:
        if t1_sel == "➕ New Creator":
            st.subheader("New Creator")
            nc_name    = st.text_input("Name", placeholder="e.g. Fiona")
            nc_collage = st.file_uploader("Collage image", type=["png", "jpg", "jpeg"],
                                          key="nc_collage")
            nc_voice   = st.text_input("ElevenLabs Voice ID")
            nc_desc    = st.text_input("Description",
                                       placeholder="Soft, intimate, slightly breathy…")
            with st.expander("Voice settings", expanded=False):
                nc_stab = st.slider("Stability",        0.0, 1.0, 0.4,  0.05, key="nc_stab")
                nc_sim  = st.slider("Similarity Boost", 0.0, 1.0, 0.75, 0.05, key="nc_sim")
                nc_sty  = st.slider("Style",            0.0, 1.0, 0.6,  0.05, key="nc_sty")

            if st.button("💾 Save Creator", type="primary",
                         disabled=not (nc_name and nc_collage and nc_voice),
                         use_container_width=True):
                ext = Path(nc_collage.name).suffix.lower() or ".jpg"
                save_creator(
                    nc_name, nc_collage.getvalue(), ext, nc_voice, nc_desc,
                    {"stability": nc_stab, "similarity_boost": nc_sim,
                     "style": nc_sty, "use_speaker_boost": True},
                )
                st.success(f"✅ Creator **{nc_name}** saved!")
                st.rerun()

        else:
            c = creators[t1_sel]
            st.subheader(t1_sel)
            if c.get("source") == "legacy":
                st.caption("_Legacy creator (defined in config.py)_")

            collage = resolve_collage(t1_sel)
            if collage:
                st.image(str(collage), width=220, caption="Identity collage")
            else:
                st.warning("No collage found. Add to `assets/model_collages/` or create "
                           "a new custom creator.")

            st.markdown(f"**Description:** {c.get('description', '—')}")
            vs = c.get("voice_settings", {})
            if vs:
                st.caption(
                    f"Stability {vs.get('stability', 0.4)} · "
                    f"Similarity {vs.get('similarity_boost', 0.75)} · "
                    f"Style {vs.get('style', 0.6)}"
                )

            scenes_for_creator = get_scenes(t1_sel)
            if scenes_for_creator:
                st.metric("Scenes", len(scenes_for_creator))

            # ── Voice ID — editable for ALL creators ─────────────────────────
            st.markdown("**Voice ID**")
            _slug_key = _slug(t1_sel)
            vc_col, vs_col = st.columns([3, 1])
            with vc_col:
                _new_vid = st.text_input(
                    "voice_id_field", label_visibility="collapsed",
                    value=c.get("voice_id", ""),
                    placeholder="ElevenLabs voice ID",
                    key=f"vid_input_{_slug_key}",
                )
            with vs_col:
                if st.button("💾 Update", key=f"vid_save_{_slug_key}",
                             use_container_width=True,
                             disabled=not _new_vid.strip()):
                    update_voice_id(t1_sel, c, _new_vid.strip())
                    st.success("Voice ID updated!")
                    st.rerun()

            # ── Collage + description — editable for custom creators ──────────
            if c.get("source") == "custom":
                with st.expander("✏️ Edit description / collage"):
                    slug      = _slug(t1_sel)
                    model_dir = MODELS_DIR / slug
                    new_desc  = st.text_input("Description", value=c.get("description", ""),
                                              key=f"ed_desc_{slug}")
                    new_col   = st.file_uploader("Replace collage",
                                                 type=["png", "jpg", "jpeg"],
                                                 key=f"ed_col_{slug}")
                    if st.button("💾 Save Changes", key=f"ed_save_{slug}",
                                 use_container_width=True):
                        cfg = json.loads((model_dir / "config.json").read_text())
                        cfg["description"] = new_desc
                        (model_dir / "config.json").write_text(json.dumps(cfg, indent=2))
                        if new_col:
                            ext = Path(new_col.name).suffix.lower() or ".jpg"
                            for old in model_dir.glob("collage.*"):
                                old.unlink()
                            (model_dir / f"collage{ext}").write_bytes(new_col.getvalue())
                        st.success("Saved!")
                        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SCENE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

with T_BUILDER:
    st.header("🎨 Scene Builder")
    st.caption("Upload reference images → generate base scenes using Nano Banana.")
    st.divider()

    creators = load_creators()
    sb_creator = st.selectbox("Creator", list(creators.keys()), key="sb_creator_sel")
    collage_path = resolve_collage(sb_creator)

    if not collage_path:
        st.error(f"No collage for **{sb_creator}**. Add one in the Creators tab.")
        st.stop()
    if not anthropic_key:
        st.warning("⚠️ ANTHROPIC_API_KEY not set — prompt generation unavailable.")
    if not wavespeed_key:
        st.warning("⚠️ WAVESPEED_API_KEY not set — image generation unavailable.")

    # ── Step 1: Upload references ─────────────────────────────────────────────
    st.subheader("1 · Upload References")
    ref_files = st.file_uploader(
        "Drop reference images (one per scene)",
        type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="sb_refs",
    )

    if ref_files and st.button("✅ Confirm Uploads", use_container_width=True):
        tmp               = Path(tempfile.mkdtemp())
        ss.sb_tmp_dir     = tmp
        ss.sb_ref_paths   = save_uploads(ref_files, tmp)
        ss.sb_prompts     = {}
        ss.sb_scene_names = {}
        st.rerun()

    # ── Step 2: Name + Prompt ────────────────────────────────────────────────
    if ss.sb_ref_paths:
        n_refs = len(ss.sb_ref_paths)
        st.divider()
        st.subheader("2 · Name & Prompt Each Scene")
        st.caption(
            f"{n_refs} reference{'s' if n_refs > 1 else ''} confirmed. "
            "Name each scene and generate its unique prompt."
        )

        can_prompt = bool(anthropic_key) and collage_path is not None
        n_done_p   = sum(1 for i in range(n_refs) if ss.sb_prompts.get(i))
        all_p_done = (n_done_p == n_refs)

        if can_prompt:
            gen_all_lbl = (
                f"🔄 Regenerate All {n_refs} Prompts" if all_p_done
                else f"🤖 Generate All {n_refs} Prompts"
            )
            if st.button(gen_all_lbl, use_container_width=True):
                master = mp_path.read_text(encoding="utf-8")
                prog   = st.progress(0.0)
                for i, ref_path in enumerate(ss.sb_ref_paths):
                    prog.progress(i / n_refs, text=f"Prompt {i + 1}/{n_refs}…")
                    try:
                        ss.sb_prompts[i] = generate_images.fill_prompt_with_claude(
                            collage_path, ref_path, master
                        )
                    except Exception as e:
                        st.error(f"Image {i + 1}: {e}")
                prog.progress(1.0, text="Done!")
                st.rerun()

        for i, ref_path in enumerate(ss.sb_ref_paths):
            saved_name = ss.sb_scene_names.get(i, "")
            has_prompt = bool(ss.sb_prompts.get(i))
            label = f"Scene {i + 1} — {ref_path.name}"
            if saved_name:
                label += f"  ·  📌 **{saved_name}**"
            if has_prompt:
                label += "  ✓"
            with st.expander(label, expanded=(not saved_name or not has_prompt)):
                ic, pc = st.columns([1, 3])
                with ic:
                    st.image(str(ref_path), width="stretch")
                with pc:
                    # ── Name ────────────────────────────────────────────────
                    nm_col, btn_col = st.columns([3, 1])
                    with nm_col:
                        typed_name = st.text_input(
                            "Scene name",
                            value=saved_name,
                            key=f"sb_sname_input_{i}",
                            placeholder="e.g. Forest, Bedroom, Gym",
                        )
                    with btn_col:
                        st.write("")   # vertical align
                        if st.button("💾 Save", key=f"sb_save_name_{i}",
                                     use_container_width=True,
                                     disabled=not typed_name.strip()):
                            ss.sb_scene_names[i] = typed_name.strip()
                            st.rerun()

                    if saved_name:
                        st.caption(f"✅ Saved as: **{saved_name}**")
                    else:
                        st.caption("⬆️ Enter a name and click **Save**.")

                    # ── Prompt ───────────────────────────────────────────────
                    if has_prompt:
                        edited = st.text_area(
                            f"prompt_{i}",
                            value=ss.sb_prompts[i],
                            height=200, key=f"sb_pa_{i}",
                            label_visibility="collapsed",
                        )
                        ss.sb_prompts[i] = edited
                        # ── Debug: identity source + final prompt preview ──────
                        with st.expander("🔍 Debug — final prompt sent to Nano Banana"):
                            _coll_disp = str(collage_path) if collage_path else "—"
                            st.caption(f"🪪 **Identity source (IMAGE 1):** `{_coll_disp}`")
                            st.caption(f"🖼️ **Scene source (IMAGE 2):** `{ref_path.name}`")
                            _full = (ss.sb_prompts[i]
                                     + generate_images._FACE_LOCK
                                     + generate_images._IDENTITY_LOCK)
                            st.caption(f"**Total prompt length:** {len(_full)} chars")
                            st.text(_full)
                    else:
                        st.caption("_(no prompt yet)_")
                        if can_prompt and st.button(
                            f"🤖 Generate prompt", key=f"sb_gp_{i}"
                        ):
                            with st.spinner("Generating…"):
                                try:
                                    master = mp_path.read_text(encoding="utf-8")
                                    ss.sb_prompts[i] = generate_images.fill_prompt_with_claude(
                                        collage_path, ref_path, master
                                    )
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))

        # ── Step 3: Generate ─────────────────────────────────────────────────
        st.divider()
        st.subheader("3 · Generate Scenes")
        prompts_ready    = all(ss.sb_prompts.get(i) for i in range(n_refs))
        scene_names_ok   = all(ss.sb_scene_names.get(i, "").strip() for i in range(n_refs))

        if not scene_names_ok:
            missing_names = [
                i + 1 for i in range(n_refs)
                if not ss.sb_scene_names.get(i, "").strip()
            ]
            st.info(
                f"Save a name for scene{'s' if len(missing_names) > 1 else ''} "
                f"{', '.join(str(n) for n in missing_names)} above to continue."
            )
        elif not prompts_ready:
            st.info("Generate all prompts above before generating.")
        elif not wavespeed_key:
            st.warning("WAVESPEED_API_KEY required.")
        else:
            if st.button(
                f"🎨 Generate {n_refs} Scene{'s' if n_refs > 1 else ''}",
                type="primary", use_container_width=True,
            ):
                names = [ss.sb_scene_names[i] for i in range(n_refs)]
                out_paths = []
                for nm in names:
                    d = scene_dir(sb_creator, nm)
                    d.mkdir(parents=True, exist_ok=True)
                    out_paths.append(d / "base.jpg")

                status_boxes = {i: st.empty() for i in range(n_refs)}
                for i in range(n_refs):
                    status_boxes[i].info(f"Scene {i + 1} ({names[i]}) — queued")

                def _scb(idx, msg):
                    if idx == -1: return
                    b = status_boxes.get(idx)
                    if b: b.info(f"Scene {idx + 1} ({names[idx]}) — {msg}")

                try:
                    results = generate_images.generate_batch_parallel(
                        ref_paths      = ss.sb_ref_paths,
                        collage_path   = collage_path,
                        prompts        = [ss.sb_prompts[i] for i in range(n_refs)],
                        max_concurrent = 4,
                        max_retries    = 2,
                        status_cb      = _scb,
                        indices        = list(range(n_refs)),
                        out_paths      = out_paths,
                    )
                    for i, r in enumerate(results):
                        if r.status == "success" and r.out_path:
                            # If result written to default path, move to scene dir
                            if r.out_path != out_paths[i] and r.out_path.exists():
                                shutil.move(str(r.out_path), str(out_paths[i]))
                            save_scene_meta(sb_creator, names[i], {
                                "creator":    sb_creator,
                                "scene_name": names[i],
                                "ref_image":  ss.sb_ref_paths[i].name,
                                "prompt":     ss.sb_prompts[i],
                                "created_at": datetime.now().isoformat(),
                            })
                            status_boxes[i].success(
                                f"Scene {i + 1} — ✅ saved as **{names[i]}**"
                            )
                        else:
                            status_boxes[i].error(
                                f"Scene {i + 1} — ❌ {(r.error or '')[:80]}"
                            )
                except Exception as e:
                    st.error(f"Generation error: {e}")

        # Results preview (all saved scenes for this creator)
        saved = get_scenes(sb_creator)
        if saved:
            st.divider()
            st.subheader(f"Saved Scenes — {sb_creator}")
            prev_cols = st.columns(min(len(saved), 5))
            for i, sname in enumerate(saved):
                base = get_base_image(sb_creator, sname)
                if base:
                    with prev_cols[i % 5]:
                        st.image(str(base), width="stretch", caption=sname)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SCENE VARIATIONS
# ══════════════════════════════════════════════════════════════════════════════

with T_VARS:
    st.header("✨ Scene Variations")
    st.caption("Take a base scene → generate 5 variations with Seedream V4.5 → save a Scene Pack.")
    st.divider()

    creators = load_creators()
    sv_creator = st.selectbox("Creator", list(creators.keys()), key="sv_creator_sel")

    scenes = get_scenes(sv_creator)
    if not scenes:
        st.info(f"No scenes for **{sv_creator}**. Go to Scene Builder first.")
        st.stop()

    sv_scene = st.selectbox("Scene", scenes, key="sv_scene_sel")

    # Reset variation state when creator or scene changes
    if sv_creator != ss.sv_creator or sv_scene != ss.sv_scene:
        ss.sv_creator     = sv_creator
        ss.sv_scene       = sv_scene
        ss.sv_var_prompts = dict(enumerate(generate_variations.VARIATION_PROMPTS))
        ss.sv_var_paths   = {}
        ss.sv_var_sel     = {}
        # Load any existing variations from disk
        for vi, vp in get_variations(sv_creator, sv_scene).items():
            ss.sv_var_paths[vi] = vp
        for vi in load_approved(sv_creator, sv_scene):
            ss.sv_var_sel[vi] = True

    base_img = get_base_image(sv_creator, sv_scene)
    if not base_img:
        st.error(f"No base image for scene **{sv_scene}**.")
        st.stop()

    # ── Header: base image + generate-all ────────────────────────────────────
    hc1, hc2 = st.columns([1, 3])
    with hc1:
        st.image(str(base_img), caption="Base image", width="stretch")
    with hc2:
        st.markdown(f"**Creator:** {sv_creator}  \n**Scene:** {sv_scene}")
        meta = load_scene_meta(sv_creator, sv_scene)
        if meta.get("created_at"):
            st.caption(f"Scene created: {meta['created_at'][:10]}")

        n_done_v = len([v for v in ss.sv_var_paths.values() if v and v.exists()])
        gen_all_lbl = (
            f"🔄 Regenerate All {N_VARS} Variations" if n_done_v >= N_VARS
            else f"⚡ Generate All {N_VARS} Variations"
        )
        if st.button(gen_all_lbl, use_container_width=True, type="primary",
                     key="sv_gen_all"):
            var_out_dir = scene_dir(sv_creator, sv_scene) / "variations"
            var_out_dir.mkdir(parents=True, exist_ok=True)
            with st.spinner(f"Generating all {N_VARS} variations…"):
                try:
                    paths = generate_variations.run_all(
                        base_img, 0, sv_creator, N_VARS,
                        out_dir=var_out_dir,
                    )
                    # Also archive to all_variations/
                    _all_dir = scene_dir(sv_creator, sv_scene) / "all_variations"
                    _all_dir.mkdir(parents=True, exist_ok=True)
                    for vi, vp in enumerate(paths):
                        ss.sv_var_paths[vi] = vp
                        ss.sv_var_sel[vi]   = True
                        shutil.copy2(str(vp), str(_all_dir / f"v_{vi:02d}.jpg"))
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

    st.divider()

    # ── Individual variation slots ────────────────────────────────────────────
    st.subheader("Variations — Generate & Refine")
    st.caption("Edit a prompt, then generate or regenerate each slot individually.")

    # Image row
    img_row = st.columns(N_VARS)
    for vi in range(N_VARS):
        vp = ss.sv_var_paths.get(vi)
        with img_row[vi]:
            if vp and vp.exists():
                st.image(str(vp), width="stretch", caption=f"V{vi + 1}")
            else:
                st.markdown(
                    f"<div style='height:200px;background:#111;border:1px dashed #444;"
                    f"border-radius:6px;display:flex;align-items:center;"
                    f"justify-content:center;color:#666;font-size:13px;flex-direction:column'>"
                    f"V{vi + 1}<br><span style='font-size:11px'>not generated</span></div>",
                    unsafe_allow_html=True,
                )

    # Prompt + button row
    ctrl_row = st.columns(N_VARS)
    for vi in range(N_VARS):
        vp = ss.sv_var_paths.get(vi)
        with ctrl_row[vi]:
            edited_p = st.text_area(
                f"V{vi + 1} prompt",
                value=ss.sv_var_prompts.get(vi, generate_variations.VARIATION_PROMPTS[vi]),
                height=140, key=f"sv_vp_{vi}",
                label_visibility="collapsed",
            )
            ss.sv_var_prompts[vi] = edited_p

            btn_lbl = "🔄 Regen" if (vp and vp.exists()) else "▶ Generate"
            if st.button(btn_lbl, key=f"sv_gen_{vi}", use_container_width=True):
                var_out_dir = scene_dir(sv_creator, sv_scene) / "variations"
                var_out_dir.mkdir(parents=True, exist_ok=True)
                custom_out  = var_out_dir / f"v_{vi:02d}.jpg"
                with st.spinner(f"Generating V{vi + 1}…"):
                    try:
                        new_vp = generate_variations.run_single(
                            base_img, 0, vi, sv_creator,
                            custom_prompt=ss.sv_var_prompts[vi],
                            out_path=custom_out,
                        )
                        ss.sv_var_paths[vi] = new_vp
                        ss.sv_var_sel[vi]   = True
                        # Archive to all_variations/
                        _all_dir = scene_dir(sv_creator, sv_scene) / "all_variations"
                        _all_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(new_vp), str(_all_dir / f"v_{vi:02d}.jpg"))
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    # ── Review & Select ───────────────────────────────────────────────────────
    generated_vars = {vi: vp for vi, vp in ss.sv_var_paths.items()
                      if vp and vp.exists()}
    if generated_vars:
        st.divider()
        st.subheader("Review & Select Scene Pack")
        st.caption(
            "Tick the images to include. "
            "**Only checked images will be used for reel generation.**"
        )

        n_sel_now = sum(1 for vi in generated_vars if ss.sv_var_sel.get(vi, True))
        sa_c, sd_c, cnt_c = st.columns([1, 1, 4])
        with sa_c:
            if st.button("✅ Select All", key="sv_sel_all", use_container_width=True):
                for vi in generated_vars:
                    ss.sv_var_sel[vi] = True
                st.rerun()
        with sd_c:
            if st.button("☐ Deselect All", key="sv_desel_all", use_container_width=True):
                for vi in generated_vars:
                    ss.sv_var_sel[vi] = False
                st.rerun()
        with cnt_c:
            st.caption(f"**{n_sel_now}** of {len(generated_vars)} selected")

        review_cols = st.columns(N_VARS)
        for vi in range(N_VARS):
            vp = ss.sv_var_paths.get(vi)
            with review_cols[vi]:
                if vp and vp.exists():
                    st.image(str(vp), width="stretch", caption=f"V{vi + 1}")
                    chk = st.checkbox(
                        "Use", value=ss.sv_var_sel.get(vi, True), key=f"sv_chk_{vi}",
                    )
                    ss.sv_var_sel[vi] = chk
                else:
                    st.caption(f"V{vi + 1} — not generated")

        st.divider()
        approved = [vi for vi in range(N_VARS) if ss.sv_var_sel.get(vi, False)]
        if st.button(
            f"💾 Save Scene Pack  ({len(approved)} image{'s' if len(approved) != 1 else ''})",
            type="primary", use_container_width=True,
            disabled=(len(approved) == 0),
        ):
            save_approved(sv_creator, sv_scene, approved)
            st.success(
                f"✅ Scene Pack saved — **{sv_creator}** / **{sv_scene}** "
                f"— {len(approved)} approved image{'s' if len(approved) != 1 else ''}."
            )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — REEL GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

with T_REELS:
    st.header("🎬 Reel Generator")
    st.caption("Select a Scene Pack → add scripts → generate reels.")
    st.divider()

    creators       = load_creators()
    rg_creator     = st.selectbox("Creator", list(creators.keys()), key="rg_creator_sel")
    rg_creator_data = creators[rg_creator]

    # ── Image source ──────────────────────────────────────────────────────────
    rg_mode = st.radio(
        "Image source",
        ["📦 Scene Pack", "📁 Upload Images"],
        horizontal=True, key="rg_mode",
    )
    st.divider()

    final_paths: list[Path] = []

    if rg_mode == "📦 Scene Pack":
        all_scenes = get_scenes(rg_creator)
        packed     = [s for s in all_scenes if load_approved(rg_creator, s)]
        if not all_scenes:
            st.info(f"No scenes for **{rg_creator}**. Build one in Scene Builder first.")
            st.stop()
        if not packed:
            st.info("No Scene Packs saved yet. Go to Scene Variations → Save Scene Pack.")
            st.stop()

        rg_scene    = st.selectbox("Scene Pack", packed, key="rg_scene_sel")
        approved_vi = load_approved(rg_creator, rg_scene)
        candidate_paths = [
            p for vi in approved_vi
            if (p := var_path(rg_creator, rg_scene, vi)).exists()
        ]
        if not candidate_paths:
            st.error("Approved files not found on disk. Regenerate in Scene Variations.")
            st.stop()

        # Reset selection whenever the scene changes
        _pack_key = f"{rg_creator}/{rg_scene}"
        if ss.rg_last_scene != _pack_key:
            ss.rg_last_scene = _pack_key
            ss.rg_sel = {i: True for i in range(len(candidate_paths))}

        st.caption(
            f"**{len(candidate_paths)}** approved images — tick which to use for reels"
        )
        _rg_sa, _rg_sd = st.columns(2)
        with _rg_sa:
            if st.button("✅ Select All", key="rg_sel_all", use_container_width=True):
                ss.rg_sel = {i: True for i in range(len(candidate_paths))}
                st.rerun()
        with _rg_sd:
            if st.button("☐ Deselect All", key="rg_desel_all", use_container_width=True):
                ss.rg_sel = {i: False for i in range(len(candidate_paths))}
                st.rerun()

        _rg_cols = st.columns(min(len(candidate_paths), 5))
        for _i, (_p, _vi) in enumerate(zip(candidate_paths, approved_vi)):
            with _rg_cols[_i % 5]:
                st.image(str(_p), width="stretch", caption=f"V{_vi + 1}")
                _chk = st.checkbox(
                    "Use", value=ss.rg_sel.get(_i, True), key=f"rg_chk_{_i}",
                )
                ss.rg_sel[_i] = _chk

        final_paths = [
            _p for _i, _p in enumerate(candidate_paths)
            if ss.rg_sel.get(_i, True)
        ]

    else:
        rg_uploads = st.file_uploader(
            "Drop final images", type=["png", "jpg", "jpeg"],
            accept_multiple_files=True, key="rg_uploads",
        )
        if rg_uploads:
            tmp         = Path(tempfile.mkdtemp())
            final_paths = save_uploads(rg_uploads, tmp)
            st.caption(f"✅ {len(final_paths)} images uploaded")
            img_grid(final_paths, n_cols=min(len(final_paths), 5))
        else:
            st.info("Upload images to proceed.")

    # ── Scripts ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Scripts")
    rg_scripts_raw = st.text_area(
        "Paste scripts — one per line",
        height=180,
        placeholder="I always say I don't care about size…\nText me good morning…",
        key="rg_scripts_area",
    )
    hooks = [h.strip() for h in rg_scripts_raw.splitlines()
             if h.strip() and not h.strip().startswith("#")]
    if hooks:
        st.caption(f"✅ {len(hooks)} script{'s' if len(hooks) > 1 else ''}")

    if final_paths and hooks and len(final_paths) != len(hooks):
        use_n = min(len(final_paths), len(hooks))
        st.warning(
            f"⚠️ {len(hooks)} scripts / {len(final_paths)} images — "
            f"first **{use_n}** pairs will run."
        )

    # ── Generate button ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Generate Reels")

    missing = []
    if not final_paths:                        missing.append("images")
    if not hooks:                              missing.append("scripts")
    if not rg_creator_data.get("voice_id"):   missing.append("voice ID")
    if not heygen_key:                         missing.append("HeyGen key")

    if missing:
        st.info(f"Still needed: **{', '.join(missing)}**")
    else:
        n_vid = min(len(final_paths), len(hooks))
        if st.button(
            f"🚀 Generate {n_vid} Reel{'s' if n_vid > 1 else ''}",
            type="primary", use_container_width=True,
        ):
            act_paths = final_paths[:n_vid]
            act_hooks = hooks[:n_vid]

            for d in ["output/voices", "output/videos", "output/final", "input"]:
                Path(d).mkdir(parents=True, exist_ok=True)
            for d in ["output/voices", "output/videos", "output/final"]:
                for f in Path(d).glob("*"):
                    if f.is_file():
                        f.unlink()

            Path(config.INPUT_HOOKS_FILE).write_text(
                "\n".join(act_hooks), encoding="utf-8"
            )
            config.ELEVENLABS_VOICE_ID = rg_creator_data["voice_id"]
            config.VOICE_SETTINGS      = rg_creator_data.get("voice_settings", {})

            bar = st.progress(0, text="Starting reel pipeline…")
            run_video_pipeline(act_paths, act_hooks, rg_creator_data, heygen_key, bar, 0)

    # ── Download ──────────────────────────────────────────────────────────────
    finals = sorted(Path("output/final").glob("final_*.mp4"))
    if finals:
        st.divider()
        dl_name = f"{rg_creator.split()[0].lower()}_reels.zip"
        st.download_button(
            label=f"⬇️ Download {len(finals)} reel{'s' if len(finals) > 1 else ''} (.zip)",
            data=make_zip(finals),
            file_name=dl_name,
            mime="application/zip",
            use_container_width=True,
            type="primary",
        )
        vid_cols = st.columns(min(3, len(finals)))
        for i, v in enumerate(finals):
            with vid_cols[i % len(vid_cols)]:
                st.video(str(v))
                st.caption(v.stem)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — HISTORY
# ══════════════════════════════════════════════════════════════════════════════

with T_HISTORY:
    st.header("📋 History")
    st.divider()

    ht_scenes, ht_reels = st.tabs(["🎨 Scenes & Packs", "🎬 Reels"])

    with ht_scenes:
        st.subheader("Scenes & Scene Packs")
        if not SCENES_DIR.exists() or not list(SCENES_DIR.rglob("base.jpg")):
            st.info("No scenes generated yet.")
        else:
            for creator_dir in sorted(SCENES_DIR.iterdir()):
                if not creator_dir.is_dir():
                    continue
                scene_dirs = sorted(
                    p.parent for p in creator_dir.rglob("base.jpg")
                )
                if not scene_dirs:
                    continue

                # Try to get display name from meta
                first_meta = {}
                for sd in scene_dirs:
                    mp = sd / "meta.json"
                    if mp.exists():
                        try:
                            first_meta = json.loads(mp.read_text())
                            break
                        except Exception:
                            pass
                creator_display = first_meta.get(
                    "creator",
                    creator_dir.name.replace("_", " ").title()
                )
                st.markdown(f"#### 👤 {creator_display}")

                for sd in scene_dirs:
                    sname = sd.name
                    # Load approved directly from path (avoids double-slug)
                    app_path = sd / "approved.json"
                    approved_vilist: list[int] = []
                    if app_path.exists():
                        try:
                            approved_vilist = json.loads(app_path.read_text())
                        except Exception:
                            pass

                    meta_path = sd / "meta.json"
                    meta_data: dict = {}
                    if meta_path.exists():
                        try:
                            meta_data = json.loads(meta_path.read_text())
                        except Exception:
                            pass

                    ci, cinfo = st.columns([1, 4])
                    with ci:
                        base = sd / "base.jpg"
                        if base.exists():
                            st.image(str(base), width="stretch")
                    with cinfo:
                        st.markdown(f"**{sname}**")
                        if meta_data.get("created_at"):
                            st.caption(f"Created: {meta_data['created_at'][:10]}")
                        if approved_vilist:
                            var_thumbs = [
                                p for vi in approved_vilist
                                if (p := sd / "variations" / f"v_{vi:02d}.jpg").exists()
                            ]
                            n_app = len(var_thumbs)
                            st.caption(f"✅ Scene Pack: {n_app} approved variation"
                                       f"{'s' if n_app != 1 else ''}")
                            if var_thumbs:
                                img_grid(var_thumbs, n_cols=min(5, len(var_thumbs)),
                                         captions=[f"V{vi + 1}" for vi in approved_vilist
                                                   if (sd / "variations" / f"v_{vi:02d}.jpg").exists()])
                        else:
                            st.caption("_(no Scene Pack saved yet)_")

                        # Download buttons
                        _dl1, _dl2 = st.columns(2)
                        _scene_id  = f"{creator_dir.name}_{sname}"
                        with _dl1:
                            if approved_vilist:
                                st.download_button(
                                    "⬇️ Download Approved",
                                    data=zip_scene(sd, approved_only=True),
                                    file_name=f"{sname}_approved.zip",
                                    mime="application/zip",
                                    use_container_width=True,
                                    key=f"hist_app_{_scene_id}",
                                )
                        with _dl2:
                            _any_img = (sd / "base.jpg").exists() or \
                                       any(sd.rglob("v_*.jpg"))
                            if _any_img:
                                st.download_button(
                                    "⬇️ Download All",
                                    data=zip_scene(sd, approved_only=False),
                                    file_name=f"{sname}_all.zip",
                                    mime="application/zip",
                                    use_container_width=True,
                                    key=f"hist_all_{_scene_id}",
                                )
                    st.markdown("---")

    with ht_reels:
        st.subheader("Generated Reels")
        finals = sorted(Path("output/final").glob("final_*.mp4"))
        if not finals:
            st.info("No reels generated yet.")
        else:
            st.caption(f"{len(finals)} reel{'s' if len(finals) > 1 else ''} available")
            reel_cols = st.columns(min(3, len(finals)))
            for i, v in enumerate(finals):
                with reel_cols[i % len(reel_cols)]:
                    st.video(str(v))
                    st.caption(v.stem)
                    with open(v, "rb") as f:
                        st.download_button(
                            f"⬇️ {v.name}",
                            data=f.read(),
                            file_name=v.name,
                            mime="video/mp4",
                            key=f"hist_dl_{i}",
                        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — INSTAGRAM → AI REEL
# ══════════════════════════════════════════════════════════════════════════════

with T_REEL_PIPE:
    st.header("🎥 Instagram → AI Reel")
    st.caption(
        "Paste Instagram reel URL(s) → extract scene → generate AI image → generate Kling video."
    )
    st.divider()

    # ── Prerequisites check ───────────────────────────────────────────────────
    _rp_missing = []
    if not wavespeed_key: _rp_missing.append("WAVESPEED_API_KEY")
    if not anthropic_key: _rp_missing.append("ANTHROPIC_API_KEY")
    if _rp_missing:
        st.error(f"Missing API keys: {', '.join(_rp_missing)}")
        st.stop()

    # ── Creator + inputs ──────────────────────────────────────────────────────
    creators = load_creators()
    rp_creator = st.selectbox("Creator (identity)", list(creators.keys()), key="rp_creator_sel")
    rp_collage = resolve_collage(rp_creator)
    if not rp_collage:
        st.error(f"No collage for **{rp_creator}**. Add one in the Creators tab.")
        st.stop()

    st.markdown("**Instagram Reel URL(s)** — one per line")
    rp_urls_raw = st.text_area(
        "reel_urls",
        placeholder="https://www.instagram.com/reel/ABC123/\nhttps://www.instagram.com/reel/XYZ456/",
        height=120,
        label_visibility="collapsed",
        key="rp_urls_input",
    )
    rp_urls = [u.strip() for u in rp_urls_raw.splitlines() if u.strip()]

    rp_duration = st.select_slider(
        "Kling video duration (seconds)", options=[5, 10], value=5, key="rp_duration"
    )

    if rp_urls:
        st.caption(f"✅ {len(rp_urls)} URL{'s' if len(rp_urls) > 1 else ''}")

    st.divider()

    # ── Run pipeline ──────────────────────────────────────────────────────────
    if not rp_urls:
        st.info("Paste at least one Instagram reel URL above.")
    elif st.button(
        f"🚀 Run Pipeline  ({len(rp_urls)} reel{'s' if len(rp_urls) > 1 else ''})",
        type="primary", use_container_width=True, key="rp_run",
    ):
        master = mp_path.read_text(encoding="utf-8")
        ss.rp_jobs = []

        rp_base_dir = Path("output/reel_pipeline")
        rp_base_dir.mkdir(parents=True, exist_ok=True)

        for url_i, url in enumerate(rp_urls):
            job_dir = rp_base_dir / f"job_{url_i:02d}"
            job_dir.mkdir(parents=True, exist_ok=True)

            job: dict = {"url": url, "error": None}
            st.markdown(f"---\n#### Reel {url_i + 1} of {len(rp_urls)}")
            st.caption(url)

            # Step 1 — Download
            with st.status("⬇️ Downloading reel…") as _s:
                try:
                    video_path = reel_pipeline.download_reel(url, job_dir / "download")
                    job["video"] = video_path
                    _s.update(label=f"✅ Downloaded — {video_path.name}", state="complete")
                except Exception as e:
                    job["error"] = f"Download failed: {e}"
                    _s.update(label=f"❌ Download failed", state="error")
                    st.error(job["error"])
                    ss.rp_jobs.append(job)
                    continue

            # Step 2 — Extract first frame
            with st.status("🖼️ Extracting first frame…") as _s:
                try:
                    frame_path = reel_pipeline.extract_first_frame(
                        video_path, job_dir / "frame.jpg"
                    )
                    job["frame"] = frame_path
                    _s.update(label="✅ Frame extracted", state="complete")
                    st.image(str(frame_path), caption="First frame (scene reference)",
                             width="stretch")
                except Exception as e:
                    job["error"] = f"Frame extraction failed: {e}"
                    _s.update(label="❌ Frame extraction failed", state="error")
                    st.error(job["error"])
                    ss.rp_jobs.append(job)
                    continue

            # Step 3 — Generate prompt with Claude
            with st.status("🤖 Generating prompt with Claude…") as _s:
                try:
                    filled_prompt = generate_images.fill_prompt_with_claude(
                        rp_collage, frame_path, master
                    )
                    job["prompt"] = filled_prompt
                    _s.update(label="✅ Prompt generated", state="complete")
                    with st.expander("🔍 Prompt sent to Nano Banana"):
                        _full_p = (filled_prompt
                                   + generate_images._FACE_LOCK
                                   + generate_images._IDENTITY_LOCK)
                        st.caption(f"Identity: `{rp_collage}`  |  Scene ref: `{frame_path.name}`")
                        st.caption(f"{len(_full_p)} chars")
                        st.text(_full_p)
                except Exception as e:
                    job["error"] = f"Prompt generation failed: {e}"
                    _s.update(label="❌ Prompt generation failed", state="error")
                    st.error(job["error"])
                    ss.rp_jobs.append(job)
                    continue

            # Step 4 — Generate AI image (Nano Banana)
            with st.status("🍌 Generating AI image (Nano Banana)…") as _s:
                try:
                    ai_image_path = job_dir / "ai_image.jpg"
                    generate_images.generate_base(
                        rp_collage, frame_path, filled_prompt, ai_image_path
                    )
                    job["image"] = ai_image_path
                    _s.update(label="✅ AI image generated", state="complete")
                    st.image(str(ai_image_path), caption="Generated AI image",
                             width="stretch")
                except Exception as e:
                    job["error"] = f"Nano Banana failed: {e}"
                    _s.update(label="❌ Nano Banana failed", state="error")
                    st.error(job["error"])
                    ss.rp_jobs.append(job)
                    continue

            # Step 5 — Generate Kling video
            with st.status("🎬 Generating Kling video…") as _s:
                try:
                    kling_out = job_dir / "kling_output.mp4"
                    reel_pipeline.generate_kling_video(
                        generated_image_path=ai_image_path,
                        original_video_path=video_path,
                        out_path=kling_out,
                        duration=rp_duration,
                    )
                    job["video_out"] = kling_out
                    _s.update(label="✅ Kling video ready", state="complete")
                    st.video(str(kling_out))
                except Exception as e:
                    job["error"] = f"Kling failed: {e}"
                    _s.update(label="❌ Kling failed", state="error")
                    st.error(job["error"])
                    ss.rp_jobs.append(job)
                    continue

            ss.rp_jobs.append(job)
            st.success(f"✅ Reel {url_i + 1} complete!")

    # ── Results + downloads ───────────────────────────────────────────────────
    done_jobs = [j for j in ss.rp_jobs if j.get("video_out") and Path(j["video_out"]).exists()]
    if done_jobs:
        st.divider()
        st.subheader(f"Results — {len(done_jobs)} reel{'s' if len(done_jobs) > 1 else ''} ready")

        # Per-reel downloads
        for i, job in enumerate(done_jobs):
            c_img, c_vid = st.columns(2)
            with c_img:
                if job.get("image") and Path(job["image"]).exists():
                    st.image(str(job["image"]), caption=f"AI image {i + 1}", width="stretch")
                    with open(job["image"], "rb") as f:
                        st.download_button(
                            f"⬇️ Image {i + 1}",
                            data=f.read(),
                            file_name=f"ai_image_{i + 1:02d}.jpg",
                            mime="image/jpeg",
                            key=f"rp_dl_img_{i}",
                        )
            with c_vid:
                st.video(str(job["video_out"]))
                with open(job["video_out"], "rb") as f:
                    st.download_button(
                        f"⬇️ Video {i + 1}",
                        data=f.read(),
                        file_name=f"kling_reel_{i + 1:02d}.mp4",
                        mime="video/mp4",
                        key=f"rp_dl_vid_{i}",
                    )

        # Batch ZIP download
        if len(done_jobs) > 1:
            st.divider()
            _zip_buf = io.BytesIO()
            with zipfile.ZipFile(_zip_buf, "w", zipfile.ZIP_DEFLATED) as _zf:
                for i, job in enumerate(done_jobs):
                    if job.get("image") and Path(job["image"]).exists():
                        _zf.write(job["image"], f"ai_image_{i + 1:02d}.jpg")
                    if job.get("video_out") and Path(job["video_out"]).exists():
                        _zf.write(job["video_out"], f"kling_reel_{i + 1:02d}.mp4")
            st.download_button(
                f"⬇️ Download all {len(done_jobs)} reels (.zip)",
                data=_zip_buf.getvalue(),
                file_name="ai_reels_batch.zip",
                mime="application/zip",
                use_container_width=True,
                type="primary",
                key="rp_dl_zip",
            )
