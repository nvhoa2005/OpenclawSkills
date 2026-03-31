#!/usr/bin/env python3
import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import subprocess
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
from meta_ads_collector import MetaAdsCollector

try:
    from langdetect import detect as detect_lang
except Exception:
    detect_lang = None

try:
    import pycountry
except Exception:
    pycountry = None

try:
    import google.generativeai as genai
except Exception:
    genai = None

from dotenv import load_dotenv
load_dotenv()


def check_ffmpeg_installed():
    """Checks if ffmpeg is installed and provides installation instructions if not."""
    if shutil.which("ffmpeg"):
        # ffmpeg is found in PATH
        return

    print("---", file=sys.stderr)
    print("ERROR: ffmpeg is not installed or not in your system's PATH.", file=sys.stderr)
    
    platform = sys.platform
    if platform == "linux" or platform == "linux2":
        # Check if it's a Debian-based system by looking for apt-get
        if shutil.which("apt-get"):
            print("This skill requires ffmpeg to process audio.", file=sys.stderr)
            print("To install it on Debian/Ubuntu, please run this command:", file=sys.stderr)
            print("\n    sudo apt-get update && sudo apt-get install -y ffmpeg\n", file=sys.stderr)
        else:
            print("Please install ffmpeg using your system's package manager.", file=sys.stderr)
    elif platform == "darwin": # macOS
        if shutil.which("brew"):
            print("This skill requires ffmpeg to process audio.", file=sys.stderr)
            print("To install it with Homebrew, please run this command:", file=sys.stderr)
            print("\n    brew install ffmpeg\n", file=sys.stderr)
        else:
            print("This skill requires ffmpeg, which can be installed with Homebrew.", file=sys.stderr)
            print("First, install Homebrew (see https://brew.sh/), then run 'brew install ffmpeg'.", file=sys.stderr)
    elif platform == "win32":
        print("This skill requires ffmpeg to process audio.", file=sys.stderr)
        print("Please download it from https://ffmpeg.org/download.html and add it to your system's PATH.", file=sys.stderr)
    else:
        print(f"Unsupported platform '{platform}'. Please install ffmpeg manually.", file=sys.stderr)

    print("---", file=sys.stderr)
    sys.exit(1)


OUTPUT_COLUMNS = [
    "ad_id_full",
    "library_id_full",
    "countries",
    "headline",
    "headline_language",
    "primary_text",
    "primary_text_language",
    "video_url",
    "duration",
    "transcript",
    "transcript_translated",
    "video_language",
    "gender_audience",
    "age_audience",
    "video_impressions",
    "top3_reach",
    "cta_text",
    "cta_type",
    "app_link",
]

VIDEO_DIR = Path("video_downloaded")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


def retry_step(step_name: str, fn, retries: int = 3):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt >= retries:
                raise RuntimeError(f"{step_name} failed after {attempt} attempts: {e}") from e
            time.sleep(min(2 * attempt, 5))
    raise RuntimeError(f"{step_name} failed: {last_err}")


def extract_page_id(page_link: str) -> Optional[str]:
    # Common Meta Ads Library pattern: ...?view_all_page_id=123456
    m = re.search(r"[?&]view_all_page_id=(\d+)", page_link)
    if m:
        return m.group(1)
    # fallback: last long numeric token
    m2 = re.search(r"(\d{5,})", page_link)
    return m2.group(1) if m2 else None


def all_country_codes() -> List[str]:
    if pycountry is not None:
        return sorted({c.alpha_2 for c in pycountry.countries if getattr(c, "alpha_2", None)})
    # fallback minimal list if pycountry is unavailable
    return ["US", "VN", "GB", "CA", "AU", "DE", "FR", "JP", "KR", "SG"]


def obj_to_dict(x: Any) -> Dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if dataclasses.is_dataclass(x):
        return dataclasses.asdict(x)
    if hasattr(x, "model_dump"):
        try:
            return x.model_dump()
        except Exception:
            pass
    if hasattr(x, "dict"):
        try:
            return x.dict()
        except Exception:
            pass
    if hasattr(x, "__dict__"):
        return dict(vars(x))
    return {}


def get_in(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k not in cur:
            return default
        cur = cur[k]
    return cur


def find_first_value(d: Any, candidate_keys: Iterable[str]) -> Optional[Any]:
    keys = set(candidate_keys)

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in keys and v not in (None, ""):
                    return v
                out = walk(v)
                if out not in (None, ""):
                    return out
        elif isinstance(x, list):
            for it in x:
                out = walk(it)
                if out not in (None, ""):
                    return out
        return None

    return walk(d)


def pick_video_url(ad_dict: Dict[str, Any]) -> Optional[str]:
    # 1) Prefer normalized creatives from meta-ads-collector
    creatives = ad_dict.get("creatives") or []
    if isinstance(creatives, list):
        for c in creatives:
            if isinstance(c, dict):
                # Prefer SD first as requested.
                v = c.get("video_sd_url") or c.get("video_url") or c.get("video_hd_url")
                if v:
                    return v

    # 2) raw snapshot videos
    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    vids = snap.get("videos") if isinstance(snap.get("videos"), list) else []
    for v in vids:
        if isinstance(v, dict):
            u = v.get("video_sd_url") or v.get("video_url") or v.get("video_hd_url")
            if u:
                return u

    # 3) generic fallback
    return find_first_value(
        ad_dict,
        [
            "video_url",
            "videoUrl",
            "video_hd_url",
            "video_sd_url",
            "video_uri",
            "source",
            "content_url",
        ],
    )


def pick_cta_text(ad_dict: Dict[str, Any]) -> str:
    creatives = ad_dict.get("creatives") or []
    if isinstance(creatives, list):
        for c in creatives:
            if isinstance(c, dict) and c.get("cta_text"):
                return str(c.get("cta_text"))

    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    if snap.get("cta_text"):
        return str(snap.get("cta_text"))
    if raw.get("cta_text"):
        return str(raw.get("cta_text"))

    return str(find_first_value(ad_dict, ["cta_text", "call_to_action_text", "ctaLabel"]) or "N/A")


def pick_cta_type(ad_dict: Dict[str, Any]) -> str:
    creatives = ad_dict.get("creatives") or []
    if isinstance(creatives, list):
        for c in creatives:
            if isinstance(c, dict) and c.get("cta_type"):
                return str(c.get("cta_type"))

    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    if snap.get("cta_type"):
        return str(snap.get("cta_type"))
    if raw.get("cta_type"):
        return str(raw.get("cta_type"))

    return str(find_first_value(ad_dict, ["cta_type", "call_to_action_type", "ctaType"]) or "N/A")


def pick_app_link(ad_dict: Dict[str, Any]) -> str:
    creatives = ad_dict.get("creatives") or []
    if isinstance(creatives, list):
        for c in creatives:
            if isinstance(c, dict) and c.get("link_url"):
                return str(c.get("link_url"))

    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    if snap.get("link_url"):
        return str(snap.get("link_url"))
    if raw.get("link_url"):
        return str(raw.get("link_url"))

    return str(find_first_value(ad_dict, ["app_link", "app_url", "landing_page_url", "link_url", "url"]) or "N/A")


def detect_text_language_with_gemini(model_names: List[str], text: str) -> str:
    t = (text or "").strip()
    if not t or t == "N/A":
        return "N/A"

    prompt = (
        "Detect the language of this text and return ONLY ISO 639-1 code in lowercase "
        "(e.g., en, vi, id, th, fr). If uncertain, return und. Text: "
        f"{t}"
    )

    def _do():
        last_err = None
        for model_name in model_names:
            try:
                model = genai.GenerativeModel(model_name)
                rsp = model.generate_content(prompt)
                code = (rsp.text or "").strip().lower()
                code = re.sub(r"[^a-z-]", "", code)
                if 2 <= len(code) <= 5:
                    return code
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"Language detect via Gemini failed: {last_err}")

    try:
        return retry_step("detect_language_gemini", _do, retries=3)
    except Exception:
        return "N/A"


def detect_text_language(text: str, gemini_models: Optional[List[str]] = None) -> str:
    t = (text or "").strip()
    if not t or t == "N/A":
        return "N/A"

    word_count = len(t.split())

    # Rule: short text (<10 words) -> Gemini, long text -> langdetect.
    if word_count < 10 and gemini_models:
        return detect_text_language_with_gemini(gemini_models, t)

    if detect_lang is None:
        return "N/A"
    try:
        return detect_lang(t)
    except Exception:
        return "N/A"


def pick_headline(ad_dict: Dict[str, Any]) -> str:
    creatives = ad_dict.get("creatives") or []
    if isinstance(creatives, list):
        for c in creatives:
            if isinstance(c, dict) and c.get("title"):
                return str(c.get("title"))

    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    if snap.get("title"):
        return str(snap.get("title"))

    return str(find_first_value(ad_dict, ["title", "headline", "ad_creative_link_titles"]) or "N/A")


def pick_primary_text(ad_dict: Dict[str, Any]) -> str:
    creatives = ad_dict.get("creatives") or []
    if isinstance(creatives, list):
        for c in creatives:
            if isinstance(c, dict) and c.get("body"):
                return str(c.get("body"))

    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
    if snap.get("body"):
        return str(snap.get("body"))

    return str(find_first_value(ad_dict, ["body", "primary_text", "ad_creative_bodies"]) or "N/A")


def pick_impressions(ad_dict: Dict[str, Any]) -> str:
    # normalized impressions
    val = ad_dict.get("impressions")
    if isinstance(val, dict):
        lo = val.get("lower_bound") or val.get("lower") or val.get("min")
        hi = val.get("upper_bound") or val.get("upper") or val.get("max")
        if lo or hi:
            return f"{lo or ''}-{hi or ''}".strip("-")

    # raw fallback from snapshot/impressions_with_index
    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    iwi = raw.get("impressions_with_index") if isinstance(raw.get("impressions_with_index"), dict) else {}
    txt = iwi.get("impressions_text")
    if txt:
        return str(txt)

    val2 = find_first_value(ad_dict, ["impressions", "impression", "impression_range", "impressions_range"])
    if isinstance(val2, dict):
        lo = val2.get("lower_bound") or val2.get("lower") or val2.get("min")
        hi = val2.get("upper_bound") or val2.get("upper") or val2.get("max")
        if lo or hi:
            return f"{lo or ''}-{hi or ''}".strip("-")
        return "N/A"
    if val2 is None:
        return "N/A"
    return str(val2)


def download_video(url: str, target: Path):
    def _do():
        with requests.get(url, timeout=90, stream=True) as r:
            r.raise_for_status()
            with target.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        f.write(chunk)

    retry_step("download_video", _do, retries=3)


def probe_duration_seconds(video_path: Path) -> str:
    def _do():
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        # Use subprocess.run for better error handling and stream management
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        if result.returncode != 0:
            # ffprobe can write to stderr even on success with some formats, so check stdout first.
            if result.stdout.strip():
                 # Try to parse stdout even if exit code is non-zero
                 pass
            else:
                # If no stdout, it's a real error.
                error_message = result.stderr or result.stdout or "ffprobe failed with no output"
                raise RuntimeError(f"ffprobe failed with exit code {result.returncode}: {error_message.strip()}")

        out = result.stdout.strip()
        return str(int(float(out))) if out else "N/A"

    try:
        return retry_step("probe_duration", _do, retries=3)
    except Exception:
        return "N/A"


def setup_gemini_models() -> List[str]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Missing GEMINI_API_KEY env var")
    if genai is None:
        raise RuntimeError("google-generativeai not installed")
    genai.configure(api_key=key)

    # User requirement: prioritize Gemini 2.5 Flash for video analysis.
    # Keep fallbacks to avoid hard failure if temporary model/API issues occur.
    return [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-flash-latest",
        "gemini-2.5-pro",
    ]


def wait_for_uploaded_file_active(file_obj, timeout_seconds: int = 120):
    start = time.time()
    name = getattr(file_obj, "name", None)
    if not name:
        return file_obj

    while True:
        current = genai.get_file(name)
        state = str(getattr(getattr(current, "state", None), "name", ""))
        if state == "ACTIVE":
            return current
        if state in {"FAILED", "STATE_UNSPECIFIED"}:
            raise RuntimeError(f"Gemini file upload failed with state={state}")
        if time.time() - start > timeout_seconds:
            raise RuntimeError(f"Gemini file did not become ACTIVE within {timeout_seconds}s (state={state})")
        time.sleep(2)


def extract_audio_from_video(video_path: Path) -> Path:
    """Extracts audio from a video file using ffmpeg and returns the path to the audio file."""
    audio_path = video_path.with_suffix(".mp3")
    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-q:a", "0",  # high quality VBR
        "-map", "a",  # audio stream
        "-y",         # overwrite output file
        str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg and ensure it's in the system's PATH.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg failed to extract audio: {e.stderr}")
    
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg ran but the output audio file is missing or empty.")
        
    return audio_path


def gemini_transcribe_and_analyze(model_names: List[str], video_path: Path) -> Dict[str, Any]:
    prompt = (
        "Transcribe this audio. Return strict JSON with keys: "
        "transcript (original language), "
        "transcript_translated (to Vietnamese), and "
        "video_language (full language name, e.g., 'English', 'Vietnamese'). "
        "If no speech, all values should be 'N/A'. "
        "Do not include markdown fences."
    )

    audio_path = None
    try:
        audio_path = extract_audio_from_video(video_path)

        def _do():
            uploaded = genai.upload_file(path=str(audio_path))
            uploaded = wait_for_uploaded_file_active(uploaded, timeout_seconds=180)
            last_err = None
            for model_name in model_names:
                try:
                    model = genai.GenerativeModel(model_name)
                    rsp = model.generate_content([prompt, uploaded])
                    txt = (rsp.text or "").strip()
                    txt = re.sub(r"^```json\s*|\s*```$", "", txt, flags=re.MULTILINE)
                    data = json.loads(txt)
                    return {
                        "transcript": data.get("transcript", "N/A") or "N/A",
                        "transcript_translated": data.get("transcript_translated", "N/A") or "N/A",
                        "video_language": data.get("video_language", "N/A") or "N/A",
                    }
                except Exception as e:
                    last_err = e
                    continue
            raise RuntimeError(f"All Gemini models failed. Last error: {last_err}")

        return retry_step("gemini_analyze_audio", _do, retries=3)
    finally:
        # Clean up the temporary audio file
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
            except OSError:
                pass


def extract_countries_from_ad(ad_dict: Dict[str, Any], fallback_country: str) -> list[str]:
    # 1) Prefer normalized `countries` field from meta_ads_collector.
    countries = ad_dict.get("countries")
    if isinstance(countries, list) and countries:
        vals = [str(x).strip() for x in countries if str(x).strip()]
        if vals:
            return sorted(set(vals))

    # 2) Fallback to raw targeted/reached countries.
    raw = ad_dict.get("raw_data") if isinstance(ad_dict.get("raw_data"), dict) else {}
    tr_countries = raw.get("targeted_or_reached_countries")
    if isinstance(tr_countries, list) and tr_countries:
        vals = [str(x).strip() for x in tr_countries if str(x).strip()]
        if vals:
            return sorted(set(vals))

    # 3) Fallback to region_distribution.
    region_dist = ad_dict.get("region_distribution")
    if isinstance(region_dist, list) and region_dist:
        vals = []
        for r in region_dist:
            if isinstance(r, dict):
                c = r.get("country") or r.get("country_code") or r.get("category")
                if c:
                    vals.append(str(c).strip())
        if vals:
            return sorted(set(vals))

    # 4) No fallback country injection.
    # If collector payload has no country signal, keep countries empty.
    return []


def pick_gender_audience(ad_dict: Dict[str, Any]) -> str:
    v = ad_dict.get("gender_audience")
    if v in (None, ""):
        return "N/A"
    return str(v)


def pick_age_audience(ad_dict: Dict[str, Any]) -> str:
    v = ad_dict.get("age_audience")
    if v in (None, ""):
        return "N/A"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def pick_eu_total_reach(ad_dict: Dict[str, Any]) -> str:
    v = ad_dict.get("eu_total_reach")
    if v in (None, ""):
        return "N/A"
    return str(v)


def parse_eu_total_reach_lower_bound(ad_dict: Dict[str, Any]) -> Optional[int]:
    v = ad_dict.get("eu_total_reach")
    if v in (None, "", "N/A"):
        return None

    if isinstance(v, (int, float)):
        try:
            return int(v)
        except Exception:
            return None

    if isinstance(v, dict):
        for k in ["lower_bound", "lower", "min", "from", "start"]:
            x = v.get(k)
            if isinstance(x, (int, float)):
                return int(x)
        return None

    s = str(v).strip()
    if not s or s.upper() == "N/A":
        return None

    nums = re.findall(r"\d+", s)
    if not nums:
        return None
    try:
        return int(nums[0])
    except Exception:
        return None


def pick_top3_reach(ad_dict: Dict[str, Any]) -> str:
    v = ad_dict.get("top3_reach")
    if v in (None, ""):
        return "N/A"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def crawl_ads_from_page(page_link: Optional[str], page_id: Optional[str], output_dir: Path, max_ads: Optional[int] = None, crawl_all_countries: bool = True) -> List[Tuple[list[str], Any]]:
    if not page_id and page_link:
        page_id = extract_page_id(page_link)
    if not page_id:
        raise ValueError("Cannot resolve page_id. Provide --page-id or a valid page link with id.")

    rows = []
    seen_ad_ids = set()

    with MetaAdsCollector() as collector:
        def _crawl_all():
            # IMPORTANT: only ACTIVE ads, and use country=ALL in a single call.
            # Keep collect_to_json because it includes normalized fields like
            # eu_total_reach / age_audience / gender_audience / countries.
            tmp_json = output_dir / f"_tmp_collect_{page_id}_ALL.json"
            try:
                collector.collect_to_json(
                    str(tmp_json),
                    query="",
                    country="ALL",
                    page_ids=[str(page_id)],
                    status="ACTIVE",
                    max_results=None,
                )
                with tmp_json.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
                ads_data = payload.get("ads") if isinstance(payload, dict) else None
                return ads_data if isinstance(ads_data, list) else []
            finally:
                try:
                    tmp_json.unlink(missing_ok=True)
                except Exception:
                    pass

        ads = retry_step("crawl_country_ALL", _crawl_all, retries=3)

        for ad in ads:
            ad_dict = obj_to_dict(ad)
            ad_key = str(find_first_value(ad_dict, ["id", "ad_id", "ad_archive_id", "library_id"]) or "")
            if not ad_key or ad_key in seen_ad_ids:
                continue
            seen_ad_ids.add(ad_key)
            derived_countries = extract_countries_from_ad(ad_dict, fallback_country="ALL")
            rows.append((derived_countries, ad))
            if max_ads is not None and len(rows) >= max_ads:
                return rows

    return rows


def canonical_video_key(video_url: Optional[str]) -> str:
    u = str(video_url or "").strip()
    if not u or u.upper() == "N/A":
        return ""
    return u.split("?", 1)[0]


def load_seen_video_keys(output_dir: Path) -> set[str]:
    p = output_dir / "video_seen_keys.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x).strip() for x in data if str(x).strip()}
    except Exception:
        pass
    return set()


def save_seen_video_keys(output_dir: Path, seen: set[str]) -> None:
    p = output_dir / "video_seen_keys.json"
    p.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def build_row(countries: list[str], ad: Any, gemini_models: List[str]) -> Dict[str, Any]:
    ad_dict = obj_to_dict(ad)

    ad_id_full = str(find_first_value(ad_dict, ["id", "ad_id", "ad_archive_id"]) or "N/A")
    library_id_full = str(find_first_value(ad_dict, ["library_id", "ad_archive_id", "id"]) or "N/A")
    headline = pick_headline(ad_dict)
    primary_text = pick_primary_text(ad_dict)
    cta_text = pick_cta_text(ad_dict)
    cta_type = pick_cta_type(ad_dict)
    app_link = pick_app_link(ad_dict)
    eu_total_reach = pick_eu_total_reach(ad_dict)
    top3_reach = pick_top3_reach(ad_dict)
    video_url = pick_video_url(ad_dict)

    row = {
        "ad_id_full": ad_id_full,
        "library_id_full": library_id_full,
        "countries": format_countries_display(countries),
        "headline": headline,
        "headline_language": detect_text_language(headline, gemini_models),
        "primary_text": primary_text,
        "primary_text_language": detect_text_language(primary_text, gemini_models),
        "video_url": video_url or "N/A",
        "duration": "N/A",
        "transcript": "N/A",
        "transcript_translated": "N/A",
        "video_language": "N/A",
        "gender_audience": pick_gender_audience(ad_dict),
        "age_audience": pick_age_audience(ad_dict),
        "video_impressions": eu_total_reach,
        "top3_reach": top3_reach,
        "cta_text": cta_text,
        "cta_type": cta_type,
        "app_link": app_link,
    }

    if not video_url or video_url == "N/A":
        return row

    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", ad_id_full)[:80]
    video_path = VIDEO_DIR / f"{safe_id}.mp4"

    try:
        retry_step("download_video", lambda: download_video(video_url, video_path), retries=3)
        row["duration"] = probe_duration_seconds(video_path)

        gem = retry_step("gemini_transcribe_and_analyze", lambda: gemini_transcribe_and_analyze(gemini_models, video_path), retries=3)
        row["transcript"] = gem["transcript"]
        row["transcript_translated"] = gem["transcript_translated"]
        row["video_language"] = gem["video_language"]
    finally:
        # Clean up the downloaded video file as it's no longer needed after audio extraction
        if video_path.exists():
            try:
                video_path.unlink()
            except OSError:
                pass

    return row


def country_code_to_name(code_or_name: str) -> str:
    s = str(code_or_name or "").strip()
    if not s:
        return ""
    # Already looks like a full name.
    if len(s) > 3:
        return s
    cc = s.upper()
    if pycountry is None:
        return cc
    try:
        c = pycountry.countries.get(alpha_2=cc)
        if c and getattr(c, "name", None):
            return c.name
    except Exception:
        pass
    return cc


def format_countries_display(countries: list[str]) -> str:
    vals = []
    for x in countries or []:
        n = country_code_to_name(str(x).strip())
        if n:
            vals.append(n)
    uniq = []
    seen = set()
    for x in vals:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return ", ".join(uniq)


def _parse_countries_cell(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v or "").strip()
    if not s or s in {"N/A", "None", "nan", "[]"}:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    # Support both comma and pipe-delimited inputs.
    if "|" in s:
        return [x.strip() for x in s.split("|") if x.strip()]
    return [x.strip() for x in s.split(",") if x.strip()]


def merge_countries_value(existing: Any, new_countries: list[str]) -> str:
    vals = _parse_countries_cell(existing)
    vals.extend([country_code_to_name(str(x).strip()) for x in (new_countries or []) if str(x).strip()])
    uniq = []
    seen = set()
    for x in vals:
        k = str(x).strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(str(x).strip())
    return ", ".join(uniq)


def format_labels_display(labels: Any) -> str:
    vals = []
    if isinstance(labels, list):
        vals = [str(x).strip() for x in labels if str(x).strip()]
    elif labels not in (None, ""):
        vals = [str(labels).strip()]

    uniq = []
    seen = set()
    for x in vals:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return ", ".join(uniq)


def _resolve_input_identity(page_link: Optional[str], page_id: Optional[str]) -> tuple[str, str]:
    resolved_page_id = page_id
    if not resolved_page_id and page_link:
        resolved_page_id = extract_page_id(page_link)
    if resolved_page_id:
        return "page-id", str(resolved_page_id)
    if page_link:
        return "page-link", str(page_link)
    return "unknown", "unknown"


def _checkpoint_path(output_dir: Path, kind: str, value: str, max_ads: Optional[int], crawl_all_countries: bool) -> Path:
    safe_value = re.sub(r"[^a-zA-Z0-9_-]", "_", str(value))[:120]
    safe_max = "all" if max_ads is None else str(max_ads)
    safe_mode = "allc" if crawl_all_countries else "early"
    return output_dir / f"dogbot_video_checkpoint_{kind}_{safe_value}_{safe_max}_{safe_mode}.json"


def _load_video_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_video_checkpoint(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def run(page_link: Optional[str], page_id: Optional[str], output_dir: Path, max_ads: Optional[int] = None, crawl_all_countries: bool = True):
    output_dir.mkdir(parents=True, exist_ok=True)
    gemini_models = setup_gemini_models()

    input_kind, input_value = _resolve_input_identity(page_link, page_id)
    ck_state_path = _checkpoint_path(output_dir, input_kind, input_value, max_ads, crawl_all_countries)
    ck_state = _load_video_checkpoint(ck_state_path)

    ads = retry_step("crawl_ads", lambda: crawl_ads_from_page(page_link, page_id, output_dir, max_ads=max_ads, crawl_all_countries=crawl_all_countries), retries=3)

    # Persist crawl output as JSON (raw-ish normalized data) right after crawl.
    crawl_records = []
    for countries, ad in ads:
        ad_dict = obj_to_dict(ad)
        crawl_records.append({"countries": countries, "ad": ad_dict})

    rows = ck_state.get("rows", []) if isinstance(ck_state.get("rows"), list) else []
    failed_rows = int(ck_state.get("failed_rows", 0) or 0)
    skipped_duplicate_videos = int(ck_state.get("skipped_duplicate_videos", 0) or 0)
    skipped_low_reach = int(ck_state.get("skipped_low_reach", 0) or 0)
    completed_ad_keys = set(str(x) for x in (ck_state.get("completed_ad_keys") or []) if str(x).strip())

    seen_video_keys = load_seen_video_keys(output_dir)
    video_key_to_row_idx: Dict[str, int] = {}
    for i, r in enumerate(rows):
        if isinstance(r, dict):
            vk = canonical_video_key(r.get("video_url"))
            if vk and vk not in video_key_to_row_idx:
                video_key_to_row_idx[vk] = i

    for idx, (countries, ad) in enumerate(ads, start=1):
        ad_dict = obj_to_dict(ad)
        ad_key = str(find_first_value(ad_dict, ["id", "ad_id", "ad_archive_id", "library_id"]) or "")
        if ad_key and ad_key in completed_ad_keys:
            continue

        # Early filter: keep only ads with eu_total_reach >= 100. Missing/NA is skipped.
        eu_reach_lb = parse_eu_total_reach_lower_bound(ad_dict)
        if eu_reach_lb is None or eu_reach_lb < 100:
            skipped_low_reach += 1
            ad_id_for_log = str(find_first_value(ad_dict, ["id", "ad_id", "ad_archive_id"]) or "Unknown")
            print(f"[INFO] Skipping ad {ad_id_for_log} due to low reach ({eu_reach_lb})...", file=sys.stderr)
            if ad_key:
                completed_ad_keys.add(ad_key)
            _save_video_checkpoint(ck_state_path, {
                "version": 1,
                "input": {"kind": input_kind, "value": input_value},
                "max_ads": max_ads,
                "crawl_all_countries": crawl_all_countries,
                "completed_ad_keys": sorted(completed_ad_keys),
                "rows": rows,
                "failed_rows": failed_rows,
                "skipped_duplicate_videos": skipped_duplicate_videos,
                "skipped_low_reach": skipped_low_reach,
                "updated_at": dt.datetime.now().isoformat(),
            })
            continue

        video_url = pick_video_url(ad_dict)
        video_key = canonical_video_key(video_url)

        if video_key and video_key in video_key_to_row_idx:
            # Duplicate video in the same run/checkpoint: merge countries into the existing row.
            row_idx = video_key_to_row_idx[video_key]
            rows[row_idx]["countries"] = merge_countries_value(rows[row_idx].get("countries", "[]"), countries)
            skipped_duplicate_videos += 1
        elif video_key and video_key in seen_video_keys:
            skipped_duplicate_videos += 1
        else:
            try:
                row = retry_step("build_row", lambda c=countries, a=ad: build_row(c, a, gemini_models), retries=3)
                rows.append(row)
                row_video_key = canonical_video_key(row.get("video_url"))
                if row_video_key:
                    seen_video_keys.add(row_video_key)
                    video_key_to_row_idx[row_video_key] = len(rows) - 1
            except Exception:
                # Do not abort the whole run on a single ad failure.
                ad_id_full = str(find_first_value(ad_dict, ["id", "ad_id", "ad_archive_id"]) or "N/A")
                library_id_full = str(find_first_value(ad_dict, ["library_id", "ad_archive_id", "id"]) or "N/A")
                headline = pick_headline(ad_dict)
                primary_text = pick_primary_text(ad_dict)
                cta_text = pick_cta_text(ad_dict)
                cta_type = pick_cta_type(ad_dict)
                app_link = pick_app_link(ad_dict)
                eu_total_reach = pick_eu_total_reach(ad_dict)
                top3_reach = pick_top3_reach(ad_dict)

                rows.append({
                    "ad_id_full": ad_id_full,
                    "library_id_full": library_id_full,
                    "countries": format_countries_display(countries),
                    "headline": headline,
                    "headline_language": detect_text_language(headline, gemini_models),
                    "primary_text": primary_text,
                    "primary_text_language": detect_text_language(primary_text, gemini_models),
                    "video_url": video_url or "N/A",
                    "duration": "N/A",
                    "transcript": "N/A",
                    "transcript_translated": "N/A",
                    "video_language": "N/A",
                    "gender_audience": pick_gender_audience(ad_dict),
                    "age_audience": pick_age_audience(ad_dict),
                    "video_impressions": eu_total_reach,
                    "top3_reach": top3_reach,
                    "cta_text": cta_text,
                    "cta_type": cta_type,
                    "app_link": app_link,
                })
                failed_rows += 1

        if ad_key:
            completed_ad_keys.add(ad_key)

        # Save per-video progress after each processed ad.
        _save_video_checkpoint(ck_state_path, {
            "version": 1,
            "input": {"kind": input_kind, "value": input_value},
            "max_ads": max_ads,
            "crawl_all_countries": crawl_all_countries,
            "completed_ad_keys": sorted(completed_ad_keys),
            "rows": rows,
            "failed_rows": failed_rows,
            "skipped_duplicate_videos": skipped_duplicate_videos,
            "skipped_low_reach": skipped_low_reach,
            "updated_at": dt.datetime.now().isoformat(),
        })

        # periodic checkpoint every 20 processed ads
        if idx % 20 == 0 and rows:
            ck = output_dir / "meta_ads_checkpoint.xlsx"
            pd.DataFrame(rows)[OUTPUT_COLUMNS].to_excel(ck, index=False)

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"meta_ads_{ts}.xlsx"
    crawl_json_path = output_dir / f"meta_ads_crawl_{ts}.json"

    def _export():
        df = pd.DataFrame(rows)
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = "N/A"
        df = df[OUTPUT_COLUMNS]
        df.to_excel(out_path, index=False)

    def _export_crawl_json():
        with crawl_json_path.open("w", encoding="utf-8") as f:
            json.dump(crawl_records, f, ensure_ascii=False, default=str, indent=2, sort_keys=True)

    retry_step("export_excel", _export, retries=3)
    retry_step("export_crawl_json", _export_crawl_json, retries=3)
    retry_step("save_seen_video_keys", lambda: save_seen_video_keys(output_dir, seen_video_keys), retries=3)

    # Completed successfully -> clear per-video resume state for this exact input profile.
    try:
        ck_state_path.unlink(missing_ok=True)
    except Exception:
        pass

    return out_path, crawl_json_path, len(rows), failed_rows, skipped_duplicate_videos, skipped_low_reach


def main():
    check_ffmpeg_installed()
    ap = argparse.ArgumentParser(description="DogBot Meta Ads video analyzer")
    ap.add_argument("--page-link", type=str, default=None)
    ap.add_argument("--page-id", type=str, default=None)
    ap.add_argument("--output-dir", type=str, default="outputs")
    ap.add_argument("--max-ads", type=int, default=None)
    ap.add_argument("--no-crawl-all-countries", action="store_false", dest="crawl_all_countries", help="Enable early-stop mode instead of full-country scan")
    ap.set_defaults(crawl_all_countries=True)
    args = ap.parse_args()

    if bool(args.page_link) == bool(args.page_id):
        raise SystemExit("Provide exactly one of --page-link or --page-id")

    out_path, crawl_json_path, total, failed_rows, skipped_duplicate_videos, skipped_low_reach = run(args.page_link, args.page_id, Path(args.output_dir), max_ads=args.max_ads, crawl_all_countries=args.crawl_all_countries)
    print(json.dumps({
        "status": "success",
        "excel_path": str(out_path),
        "crawl_json_path": str(crawl_json_path),
        "rows_total": total,
        "failed_rows": failed_rows,
        "skipped_duplicate_videos": skipped_duplicate_videos,
        "skipped_low_reach": skipped_low_reach,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
