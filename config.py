INPUT_HOOKS_FILE = "input/hooks.txt"
ASSETS_IMAGES_DIR = "assets/images"
OUTPUT_VOICES_DIR = "output/voices"
OUTPUT_VIDEOS_DIR = "output/videos"
OUTPUT_FINAL_DIR = "output/final"
OUTPUT_GENERATED_IMAGES_DIR = "output/generated_images"
VIDEO_PROVIDER = "heygen"

# --- IMAGE GENERATION (Wavespeed / Nano Banana) ---
MASTER_PROMPT_PATH = "assets/master_prompt.txt"
MODEL_COLLAGES_DIR = "assets/model_collages"

# --- CREATORS ---
# Each creator has a voice ID and voice settings.
# Voice IDs are loaded from .env so they don't need to change here.
import os

CREATORS = {
    "Fiona (US)": {
        "voice_id": os.getenv("FIONA_VOICE_ID", "H3wCehef3yQw04YPXZRE"),
        "description": "Soft, intimate, slightly breathy and flirtatious",
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.75,
            "style": 0.6,
            "use_speaker_boost": True,
        },
    },
    "Lisa (AU)": {
        "voice_id": os.getenv("LISA_VOICE_ID", ""),
        "description": "Playful, teasing, intimate Australian accent",
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.8,
            "style": 0.65,
            "use_speaker_boost": True,
        },
    },
}

# Active voice — set at runtime by app.py or pipeline.py
ELEVENLABS_VOICE_ID = CREATORS["Fiona (US)"]["voice_id"]
VOICE_SETTINGS = CREATORS["Fiona (US)"]["voice_settings"]

# --- TIKTOK SLIDE PIPELINE ---
TIKTOK_INPUT_IMAGES_DIR = "input/images"
TIKTOK_OUTPUT_DIR = "output/slides"
TIKTOK_FONT_PATH = "assets/fonts/Montserrat-Black.ttf"
TIKTOK_FONT_BOLD_PATH = "assets/fonts/Montserrat-Bold.ttf"
SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1920
TIKTOK_MODEL = "claude-opus-4-7"

TIKTOK_CREATORS = {
    "Fiona (US)": {
        "age": 24,
        "tone": "confident, direct, aspirational — a young American woman who figured out money early and wants to share what works",
        "handle": "@fionatalksfinance",
        "overlay_opacity": 160,       # 0-255
        "accent_color": (212, 175, 55),  # gold
    },
    "Lisa (AU)": {
        "age": 26,
        "tone": "playful, casual, relatable — an Australian woman who's unexpectedly good with money and talks about it like a friend",
        "handle": "@lisaonmoney",
        "overlay_opacity": 150,
        "accent_color": (255, 255, 255),
    },
}

# --- HEYGEN AVATARS ---
# Pre-registered in HeyGen dashboard. Add new avatar IDs here as Fiona's
# image library grows. The pipeline cycles through them automatically.
HEYGEN_AVATAR_IDS = [
    "6b257c9fa52641a3b050b07fba15a548",
    "54a673b721f54f5b9e6347b179eebb20",
    "2dd148cc9b954a718231392dedb4ddb3",
]
