"""Subtitle discovery, download, and parsing helpers."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime

import yt_dlp

from bot.downloader_validation import sanitize_filename

COOKIES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cookies.txt",
)


def get_available_subtitles(info: dict) -> dict:
    """Return available subtitle info from a yt-dlp info dict."""

    if not info or not isinstance(info, dict):
        return {'manual': {}, 'auto': {}, 'has_any': False, 'original_lang': None}

    original_lang = info.get('language') or None
    priority_langs = ['pl', 'en']

    def sort_languages(langs_dict, limit=None):
        if not langs_dict:
            return []

        seen = set()
        result = []
        for lang in priority_langs:
            if lang in langs_dict and lang not in seen:
                result.append(lang)
                seen.add(lang)
        if original_lang and original_lang in langs_dict and original_lang not in seen:
            result.append(original_lang)
            seen.add(original_lang)
        rest = sorted(lang for lang in langs_dict if lang not in seen)
        result.extend(rest)
        if limit:
            result = result[:limit]
        return result

    manual_subs = info.get('subtitles') or {}
    auto_subs = info.get('automatic_captions') or {}

    manual_sorted = sort_languages(manual_subs, limit=6)
    auto_target = []
    for lang in priority_langs:
        if lang in auto_subs:
            auto_target.append(lang)
    if original_lang and original_lang in auto_subs and original_lang not in auto_target:
        auto_target.append(original_lang)

    manual = {lang: manual_subs[lang] for lang in manual_sorted}
    auto = {lang: auto_subs[lang] for lang in auto_target}

    return {
        'manual': manual,
        'auto': auto,
        'has_any': bool(manual or auto),
        'original_lang': original_lang,
    }


def download_subtitles(url, lang, output_dir, auto=False, title=""):
    """Download subtitles via yt-dlp with skip_download=True."""

    try:
        safe_title = sanitize_filename(title) if title else "subtitles"
        current_date = datetime.now().strftime("%Y-%m-%d")
        output_template = os.path.join(output_dir, f"{current_date} {safe_title}")

        ydl_opts = {
            'skip_download': True,
            'writesubtitles': not auto,
            'writeautomaticsub': auto,
            'subtitleslangs': [lang],
            'subtitlesformat': 'vtt/srt/best',
            'outtmpl': f"{output_template}.%(ext)s",
            'quiet': True,
            'no_warnings': True,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        for ext in ['vtt', 'srt', 'ass', 'json3', 'srv1', 'srv2', 'srv3', 'lrc']:
            candidate = f"{output_template}.{lang}.{ext}"
            if os.path.exists(candidate):
                return candidate

        logging.warning("Subtitle file not found after download for lang=%s, auto=%s", lang, auto)
        return None
    except Exception as e:
        logging.error("Error downloading subtitles: %s", e)
        return None


def parse_subtitle_file(file_path: str) -> str:
    """Parse a VTT/SRT subtitle file into clean plain text."""

    if not file_path or not os.path.exists(file_path):
        return ""

    with open(file_path, 'r', encoding='utf-8') as file_obj:
        content = file_obj.read()

    lines = content.split('\n')
    text_lines = []

    timestamp_pattern = re.compile(
        r'^\d{1,2}:\d{2}:\d{2}[.,]\d{2,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{2,3}'
    )
    sequence_pattern = re.compile(r'^\d+$')
    html_tag_pattern = re.compile(r'<[^>]+>')

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('WEBVTT'):
            continue
        if stripped.startswith('NOTE'):
            continue
        if stripped.startswith('STYLE'):
            continue
        if stripped.startswith('Kind:') or stripped.startswith('Language:'):
            continue
        if timestamp_pattern.match(stripped):
            continue
        if sequence_pattern.match(stripped):
            continue

        cleaned = html_tag_pattern.sub('', stripped).strip()
        if not cleaned:
            continue
        if text_lines and text_lines[-1] == cleaned:
            continue
        text_lines.append(cleaned)

    return '\n'.join(text_lines)
