#!/usr/bin/env python3
import argparse
import json
import os
import time
from datetime import datetime
from typing import Optional

import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv
import yt_dlp

from custom_logger import log
from constants import GEMINI_PROMPT_TEMPLATE, DEFAULT_MODEL, VIDEO_MODEL
from pytubefix import YouTube

load_dotenv()

# ==========================================
# 1. ĐỊNH NGHĨA LƯỚI LỌC PYDANTIC (SCHEMA)
# ==========================================
class AdCreativeData(BaseModel):
    ad_id: Optional[str] = Field(description="Trích xuất từ tham số 'id=' trong link")
    original_post_link: Optional[str] = Field(description="Đường link gốc của bài post")
    link_youtube: Optional[str] = Field(description="Link youtube nếu có")
    network: Optional[str] = Field(description="Nền tảng quảng cáo")
    language: Optional[str] = Field(description="Ngôn ngữ")
    region: Optional[str] = Field(description="Quốc gia hoặc Khu vực")
    duration: Optional[str] = Field(description="Thời lượng video")
    start_date: Optional[str] = Field(description="Ngày bắt đầu")
    end_date: Optional[str] = Field(description="Ngày kết thúc")
    impression: Optional[str] = Field(description="Số lượt hiển thị (Impression)")
    
    top_1_percent_creative: bool = Field(description="Điền true nếu là top 1% creative")
    top_10_percent_creative: bool = Field(description="Điền true nếu là top 10% creative")
    
    headline: Optional[str] = Field(description="Tiêu đề của quảng cáo")
    headline_language: Optional[str] = Field(description="Ngôn ngữ của headline vừa lấy được(VD: en, vi, zh...)")
    headline_translated: Optional[str] = Field(description="Dịch sang Tiếng Việt phần headline vừa lấy được")
    
    description: Optional[str] = Field(description="Mô tả nội dung của quảng cáo")
    description_language: Optional[str] = Field(description="Ngôn ngữ của description vừa lấy được (VD: en, vi...)")
    description_translated: Optional[str] = Field(description="Dịch sang Tiếng Việt phần description vừa lấy được")

    transcript: Optional[str] = Field(description="Nội dung lời thoại video. Nếu không có thì để null.")
    transcript_language: Optional[str] = Field(description="Ngôn ngữ của transcript vừa lấy được. Nếu không có thì để null.")
    transcript_translated: Optional[str] = Field(description="Dịch sang Tiếng Việt phần transcript vừa lấy được")

class AudioTranscriptData(BaseModel):
    transcript: Optional[str] = Field(description="Nội dung lời thoại (transcript) của âm thanh. Không có thoại thì null.")
    transcript_language: Optional[str] = Field(description="Ngôn ngữ của transcript vừa lấy được.(VD: en, vi...).")
    transcript_translated: Optional[str] = Field(description="Dịch sang Tiếng Việt phần transcript vừa lấy được")

# ==========================================
# 2. XỬ LÝ AUDIO YOUTUBE (TỐI ƯU TỐC ĐỘ)
# ==========================================
def download_youtube_audio(url: str, output_dir="temp_audio") -> str:
    """Chỉ tải âm thanh YouTube (chất lượng tốt nhất có sẵn) để xử lý cực nhanh."""
    os.makedirs(output_dir, exist_ok=True)
    ydl_opts = {
        'format': 'bestaudio[abr<=128][ext=m4a]/bestaudio[ext=m4a]/best',
        'outtmpl': f'{output_dir}/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'client': ['android', 'ios']}}
    }
    try:
        log.info(f"       -> Đang tải Audio YouTube: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        log.error(f"       -> [LỖI] Không thể tải audio từ Youtube: {e}")
        return None

def process_audio_with_gemini(audio_path: str) -> dict:
    """Upload âm thanh lên Gemini và bóc tách lời thoại."""
    log.info(f"       -> Đang upload file audio lên hệ thống Gemini...")
    audio_file = genai.upload_file(path=audio_path)
    
    # Đợi Gemini xử lý xong file audio
    while audio_file.state.name == "PROCESSING":
        time.sleep(2)
        audio_file = genai.get_file(audio_file.name)
        
    if audio_file.state.name == "FAILED":
        log.error("       -> [LỖI] Gemini xử lý file audio thất bại.")
        genai.delete_file(audio_file.name)
        return {"transcript": None, "transcript_language": None, "transcript_translated": None}
        
    log.info(f"       -> Đang bóc tách Transcript bằng model {VIDEO_MODEL}...")
    model = genai.GenerativeModel(VIDEO_MODEL)
    # Cập nhật lại prompt cho sát với file âm thanh
    prompt = "Hãy nghe kỹ tệp âm thanh này, trích xuất lại toàn bộ lời thoại (nếu có), xác định ngôn ngữ của nó và dịch sang tiếng Việt."
    
    try:
        response = model.generate_content(
            [audio_file, prompt],
            generation_config=GenerationConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=AudioTranscriptData,
            )
        )
        parsed_data = AudioTranscriptData.model_validate_json(response.text).model_dump()
    except Exception as e:
        log.error(f"       -> [LỖI] Khi lấy Transcript từ Gemini: {e}")
        parsed_data = {"transcript": None, "transcript_language": None, "transcript_translated": None}
    finally:
        # Xóa file trên server Gemini để tránh đầy Quota
        genai.delete_file(audio_file.name)
        
    return parsed_data

# ==========================================
# 3. HÀM GỌI GEMINI CHO HTML
# ==========================================
def parse_html_with_gemini(html: str, model_name: str) -> dict:
    prompt = GEMINI_PROMPT_TEMPLATE.format(html=html)
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            temperature=0.1, 
            response_mime_type="application/json",
            response_schema=AdCreativeData, 
        )
    )
    try:
        parsed_data = AdCreativeData.model_validate_json(response.text)
        return parsed_data.model_dump()
    except ValidationError as e:
        raise ValueError(f"Dữ liệu Gemini trả về không khớp cấu trúc: {e}")

# ==========================================
# 4. LUỒNG CHẠY BÓC TÁCH BUNDLE (MAIN)
# ==========================================
def process_bundle(input_filepath: str, api_key: str, model_name: str):
    genai.configure(api_key=api_key)
    
    with open(input_filepath, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
    
    run_id = bundle.get("run_id", "unknown_run")
    total_apps_in_bundle = bundle.get("total_apps", 0)
    
    final_output = {
        "run_id": run_id,
        "parsed_at": datetime.now().isoformat(),
        "total_apps": total_apps_in_bundle,
        "successful_apps": 0,
        "apps": []
    }

    log.info(f"Bắt đầu Parse Bundle: {run_id} | Tổng số Apps: {total_apps_in_bundle}")
    successful_apps_count = 0

    for app in bundle.get("apps", []):
        app_id = app.get("app_id")
        log.info(f"-> Đang xử lý App: {app_id}")
        
        parsed_app = {
            "app_id": app_id,
            "filters_applied": app.get("filters_applied", []),
            "scrape_statistics": app.get("scrape_statistics", {}), 
            "parse_statistics": {}, 
            "ads": []
        }
        
        total_received = len(app.get("ads", []))
        success_count = 0
        fail_count = 0
        
        for ad in app.get("ads", []):
            log.info(f"   + Bóc tách Ad Index {ad.get('ad_index')} (Trang {ad.get('page_number')})... ")
            raw_html = ad.get("raw_html", "")
            
            ad_result = {
                "ad_index": ad.get("ad_index"),
                "page_number": ad.get("page_number"),
                "captured_at": ad.get("captured_at"),
                "raw_html_length": len(raw_html),
                "gemini_data": None,
                "error": None
            }

            if not raw_html:
                ad_result["error"] = "HTML rỗng"
                fail_count += 1
                log.warning("     [BỎ QUA] HTML rỗng.")
            else:
                try:
                    # BƯỚC 1: PARSE HTML
                    gemini_html_data = parse_html_with_gemini(raw_html, model_name)
                    
                    # BƯỚC 2: XỬ LÝ YOUTUBE AUDIO (NẾU CÓ)
                    # link_yt = gemini_html_data.get("link_youtube")
                    # if link_yt and ("youtube.com" in link_yt or "youtu.be" in link_yt):
                    #     audio_path = download_youtube_audio(link_yt)
                    #     if audio_path:
                    #         transcript_data = process_audio_with_gemini(audio_path)
                    #         gemini_html_data.update(transcript_data)
                            
                    #         # Dọn dẹp file audio local
                    #         if os.path.exists(audio_path):
                    #             os.remove(audio_path)
                    
                    ad_result["gemini_data"] = gemini_html_data
                    success_count += 1
                    log.info("     [THÀNH CÔNG]")
                except Exception as e:
                    ad_result["error"] = str(e)
                    fail_count += 1
                    log.error(f"     [LỖI] {e}")
                
                time.sleep(1.5)
            
            parsed_app["ads"].append(ad_result)
            
        parsed_app["parse_statistics"] = {
            "total_ads_received": total_received,
            "successfully_parsed_ads": success_count,
            "failed_to_parse": fail_count,
            "parse_success_rate": f"{success_count}/{total_received}" if total_received > 0 else "0/0"
        }
        
        if success_count > 0:
            successful_apps_count += 1
            
        final_output["apps"].append(parsed_app)

    # Cập nhật số lượng app thành công
    final_output["successful_apps"] = successful_apps_count

    output_filename = f"final_result_{run_id}.json"
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)
        
    log.info(f"Hoàn thành! Đã lưu kết quả tại: {output_filename}")

    return output_filename

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse Raw Bundle using Gemini Structured Outputs")
    parser.add_argument("input_file", type=str, help="Đường dẫn đến file raw_bundle_...json")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("Thiếu GEMINI_API_KEY trong environment")
        raise EnvironmentError("Thiếu GEMINI_API_KEY trong environment")

    process_bundle(args.input_file, api_key, args.model)