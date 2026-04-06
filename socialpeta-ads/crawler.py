from playwright.sync_api import sync_playwright
import json
import os
import re
import random
import time
from datetime import datetime
from dotenv import load_dotenv
from playwright_stealth import Stealth

from custom_logger import log
from constants import (
    TARGET_URL, DEFAULT_TIMEOUT, LONG_TIMEOUT, Selectors, 
    DROPDOWN_SORTS, TIME_FILTERS, SORT_FILTERS, 
    VIEWPORT_WIDTH, VIEWPORT_HEIGHT, DEFAULT_MODEL
)
from human_behavior import (
    human_click, human_click_safe_zone, human_type, human_smooth_scroll, 
    human_delay, human_idle_mouse_move, show_mouse_cursor, human_aimless_highlight,
    human_wait_with_jitter, human_reading_trace, human_retreat_mouse,
    human_navigate_to_top, human_navigate_to_bottom,
    human_close_modal
)
from parse_with_gemini import process_bundle
from export_excel import json_to_excel

load_dotenv()

def read_config_json(filepath):
    if not os.path.exists(filepath):
        log.error(f"Không tìm thấy file '{filepath}'. Tạo file mẫu...")
        sample_data = [{"app_id": "com.gametree.lhlr.gp", "time_val": "90 Days", "sort_val": "Impression", "max_ads": 100, "start_page": 1}]
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(sample_data, f, indent=2)
        return []
    
    with open(filepath, 'r', encoding='utf-8') as file:
        raw_tasks = json.load(file)
    
    validated_tasks = []
    for task in raw_tasks:
        if "app_id" not in task:
            continue
            
        app_id = task["app_id"]
        
        time_val = task.get("time_val", "90 Days")
        if time_val not in TIME_FILTERS:
            log.warning(f"App {app_id}: time_val '{time_val}' không hợp lệ. Đổi về default '90 Days'.")
            time_val = "90 Days"
            
        sort_val = task.get("sort_val", "Impression")
        if sort_val not in SORT_FILTERS and sort_val not in DROPDOWN_SORTS:
            log.warning(f"App {app_id}: sort_val '{sort_val}' không hợp lệ. Đổi về default 'Impression'.")
            sort_val = "Impression"
            
        validated_tasks.append({
            "app_id": app_id,
            "time_val": time_val,
            "sort_val": sort_val,
            "max_ads": int(task.get("max_ads", 100)),
            "start_page": int(task.get("start_page", 1))
        })
        
    return validated_tasks


def view_and_extract_ads(page, app_data_dict, current_page_number, n):
    log.info(f"Đang chờ thẻ quảng cáo hiển thị (Trang {current_page_number}). Cần lấy thêm {n} thẻ...")
    
    ad_cards = page.locator(Selectors.AD_CARD)
    limit = min(n, ad_cards.count()) 
    if limit == 0: return 0

    human_aimless_highlight(page, probability=0.3)

    # Chunked Randomization
    randomized_indices = []
    for start_idx in range(0, limit, 5):
        chunk = list(range(start_idx, min(start_idx + 5, limit)))
        random.shuffle(chunk) 
        randomized_indices.extend(chunk)

    ads_collected_this_page = 0

    for order_idx, target_ad_idx in enumerate(randomized_indices):
        log.info(f"   + [Trang {current_page_number}] Xử lý thẻ {target_ad_idx + 1}...")
        card = ad_cards.nth(target_ad_idx)
        
        try: card.wait_for(state="visible", timeout=LONG_TIMEOUT)
        except Exception: continue
            
        human_smooth_scroll(page, card)

        # Chờ content load
        try:
            skeleton = card.locator(Selectors.SKELETON_LOADER)
            if skeleton.count() > 0: skeleton.first.wait_for(state="hidden", timeout=DEFAULT_TIMEOUT)
            
            media = card.locator(Selectors.MEDIA_CONTENT).first
            if media.count() > 0: media.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            else:
                text_content = card.locator(Selectors.ANY_TEXT_DIV).filter(has_text=re.compile(r".+")).first
                text_content.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        except Exception:
            log.debug(f"Bỏ qua chờ thẻ {target_ad_idx + 1}, tiếp tục.")

        human_wait_with_jitter(page, 1.5, 2.5)
        human_idle_mouse_move(page, probability=0.5)
        
        # Mở popup
        modal = page.locator(Selectors.MODAL_CONTENT).first
        popup_opened = False
        for attempt in range(3):
            human_click_safe_zone(card)
            try:
                modal.wait_for(state="visible", timeout=8000)
                popup_opened = True
                break
            except Exception:
                human_delay(1.0, 2.0)

        if popup_opened:
            try:
                log.info(f"     -> Mở modal thành công, đang chờ render dữ liệu...")
                
                skeleton = modal.locator(".ant-skeleton")
                if skeleton.count() > 0:
                    try:
                        skeleton.last.wait_for(state="hidden", timeout=15000)
                    except Exception:
                        pass 
                
                tabs_nav = modal.locator(".ant-tabs-nav").first
                if tabs_nav.count() > 0:
                    try:
                        tabs_nav.wait_for(state="visible", timeout=10000)
                    except Exception:
                        log.warning(f"     -> Không thấy thanh Tabs, có thể giao diện ad này khác biệt.")
                
                human_wait_with_jitter(page, 1.5, 2.5) 
                
                if random.random() < 0.6:
                    human_reading_trace(page, modal)
                human_wait_with_jitter(page, 2.0, 3.0)
                human_idle_mouse_move(page, probability=0.3)
                
                raw_html = modal.inner_html()
                
                if len(raw_html) < 1000:
                    log.warning(f"     -> CẢNH BÁO: HTML thẻ {target_ad_idx + 1} quá ngắn ({len(raw_html)} ký tự).")
                
                app_data_dict["ads"].append({
                    "ad_index": target_ad_idx + 1,      
                    "process_order": order_idx + 1,    
                    "page_number": current_page_number,
                    "captured_at": datetime.now().isoformat(),
                    "raw_html": raw_html
                })
                ads_collected_this_page += 1
                log.info(f"     -> Lấy HTML thẻ {target_ad_idx + 1} thành công ({len(raw_html)} ký tự).")

            except Exception as e:
                log.error(f"     -> Lỗi khi lấy nội dung thẻ {target_ad_idx + 1}: {e}")
            
            # Đóng thẻ
            close_btn = page.get_by_role("button", name="Close")
            human_close_modal(page, close_btn)
            human_retreat_mouse(page)
            human_wait_with_jitter(page, 1.0, 2.0)
            human_idle_mouse_move(page, probability=0.5)

    return ads_collected_this_page


def run(api_tasks=None, custom_run_id=None):
    run_id = custom_run_id or datetime.now().strftime("crawl_%Y%m%d_%H%M%S")
    tasks = read_config_json("crawl_app.json")

    if api_tasks is not None:
        log.debug("Lấy dữ liệu từ API.")
        tasks = api_tasks
    else:
        log.debug("Lấy dữ liệu từ crawl_app.json.")
        tasks = read_config_json("crawl_app.json")
        
    if not tasks: 
        log.error("Không có dữ liệu task. Hủy chạy.")
        return

    bundle_data = {"run_id": run_id, "total_apps": len(tasks), "apps": []}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            "./profile-chrome", headless=False, no_viewport=True,
            args=["--disable-blink-features=AutomationControlled", f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}"]
        )
        page = context.pages[0]
        Stealth().apply_stealth_sync(page)
        page.goto(TARGET_URL)
        show_mouse_cursor(page)
        human_wait_with_jitter(page, 1.0, 2.5)

        for index, task in enumerate(tasks):
            app_id, time_val, sort_val, max_ads, start_page = task["app_id"], task["time_val"], task["sort_val"], task["max_ads"], task["start_page"]
            
            log.info(f"=== XỬ LÝ APP [{index + 1}/{len(tasks)}]: {app_id} | Config: {time_val} - {sort_val} - {max_ads} ads - Page {start_page} ===")
            app_data = {
                "app_id": app_id, "filters_applied": [time_val, sort_val],
                "scrape_statistics": {"requested_max_ads": max_ads, "total_attempted_ads": 0, "successfully_scraped_ads": 0, "success_rate": "0/0"},
                "ads": []
            }
            
            human_navigate_to_top(page)
            human_delay(1.0, 1.5)
            
            # Xóa filter cũ
            if index > 0:
                clear_btn = page.locator(Selectors.CLEAR_BTN).filter(has_text="Clear")
                if clear_btn.count() > 0 and clear_btn.is_visible():
                    human_click(clear_btn.first)
                    human_wait_with_jitter(page, 1.0, 1.5)
            
            # Nhập App ID
            input_box = page.locator(Selectors.SEARCH_INPUT)
            human_click(input_box)
            human_wait_with_jitter(page, 0.5, 1.0)
            input_box.press("ControlOrMeta+a")
            input_box.press("Backspace")
            human_type(input_box, app_id)
            human_wait_with_jitter(page, 0.5, 2.0) 

            log.info(f"Đang chờ hệ thống SocialPeta tìm kiếm App ID: {app_id}...")
            app_option = page.locator(Selectors.CHOOSE_APP).filter(has_text=app_id).first
            try:
                app_option.wait_for(state="visible", timeout=15000)
                human_click(app_option)
                log.info(f"Đã click chọn thành công App: {app_id}")
            except Exception:
                log.error(f"Quá 15 giây vẫn không tìm thấy kết quả cho App {app_id}. Bỏ qua.")
                continue
            
            human_delay(1.0, 1.5)

            log.info("Đang chọn Platform: YouTube...")
            platform_btn = page.locator(Selectors.PLATFORM_MORE_BTN)
            if platform_btn.count() > 0 and platform_btn.is_visible():
                human_click(platform_btn.first)
                human_wait_with_jitter(page, 1.0, 2.0)
                
                youtube_label = page.locator(Selectors.PLATFORM_YOUTUBE_CHECKBOX).first
                try:
                    youtube_label.wait_for(state="visible", timeout=5000)
                    youtube_input = youtube_label.locator("input.ant-checkbox-input")
                    if not youtube_input.is_checked():
                        human_click(youtube_label)
                        human_wait_with_jitter(page, 0.5, 1.5)
                    else:
                        log.info("Platform YouTube đã được tick sẵn từ trước.")
                        
                    ok_btn = page.locator(Selectors.PLATFORM_OK_BTN).first
                    if ok_btn.count() > 0 and ok_btn.is_visible():
                        human_click(ok_btn)
                        human_wait_with_jitter(page, 1.0, 2.0)
                        
                except Exception as e:
                    log.error(f"Lỗi khi tick chọn YouTube (Có thể do DOM thay đổi hoặc popup không mở): {e}")
                    page.keyboard.press("Escape")
                    human_delay(0.5, 1.0)
            else:
                log.warning("Không tìm thấy nút More Platform trên giao diện.")

            # Chọn Filter
            human_click(page.get_by_text(time_val, exact=True))
            human_wait_with_jitter(page, 3.0, 5.0)

            if sort_val in DROPDOWN_SORTS:
                more_btn = page.locator(Selectors.MORE_DROPDOWN_BTN).filter(has=page.locator(Selectors.MORE_ICON))
                if more_btn.count() > 0:
                    human_click(more_btn.first) 
                    human_delay(0.5, 1.0)
                    sort_option = page.locator(Selectors.DROPDOWN_MENU).get_by_text(sort_val, exact=True)
                    if sort_option.count() > 0: human_click(sort_option.first)
            else:
                sort_option = page.get_by_text(sort_val, exact=True)
                if sort_option.count() > 0: human_click(sort_option.first)

            human_retreat_mouse(page)
            human_wait_with_jitter(page, 1.5, 2.5)
            human_idle_mouse_move(page, probability=0.7)
            
            try: page.wait_for_selector(Selectors.AD_CARD, state="visible", timeout=DEFAULT_TIMEOUT)
            except Exception:
                log.error("Không tìm thấy thẻ quảng cáo nào. Bỏ qua.")
                bundle_data["apps"].append(app_data)
                continue

            # Nhảy trang
            current_page = 1
            if start_page > 1:
                human_navigate_to_bottom(page)
                human_wait_with_jitter(page, 1.0, 2.0)
                target_page_btn = page.locator(Selectors.PAGE_BTN_TEMPLATE.format(start_page))
                if target_page_btn.count() > 0 and target_page_btn.is_visible():
                    human_click(target_page_btn.first)
                    human_delay(1.5, 2.5)
                    current_page = start_page
                    human_navigate_to_top(page)
                    human_wait_with_jitter(page, 1.0, 2.0)
                else:
                    log.error(f"Lỗi: start_page ({start_page}) không hợp lệ. Bỏ qua.")
                    continue

            # Vòng lặp cào
            total_ads_collected = 0
            session_ads_counter = 0
            while total_ads_collected < max_ads:
                try: page.wait_for_selector(Selectors.AD_CARD, state="visible", timeout=DEFAULT_TIMEOUT)
                except Exception: break

                # Cứ cào được khoảng 30 thẻ là dừng lại nghỉ ngơi
                if session_ads_counter >= random.randint(25, 35):
                    pause_time = random.uniform(20.0, 40.0)
                    log.info(f"Hành vi: Mất tập trung (Đi vệ sinh/Lướt điện thoại). Nghỉ {pause_time:.1f}s...")
                    human_idle_mouse_move(page, probability=0.8)
                    time.sleep(pause_time)
                    session_ads_counter = 0
                    
                cards_on_page = page.locator(Selectors.AD_CARD).count()
                if cards_on_page == 0: break
                
                cards_to_get = min(cards_on_page, max_ads - total_ads_collected)
                app_data["scrape_statistics"]["total_attempted_ads"] += cards_to_get
                
                collected = view_and_extract_ads(page, app_data, current_page, cards_to_get)
                total_ads_collected += collected
                session_ads_counter += collected
                
                if total_ads_collected >= max_ads: break
                    
                human_navigate_to_bottom(page)
                human_wait_with_jitter(page, 0.5, 1.5)
                next_page_btn = page.locator(Selectors.NEXT_PAGE_BTN)
                
                if next_page_btn.count() > 0 and next_page_btn.is_visible():
                    human_click(next_page_btn.first)
                    human_delay(2.0, 3.0)
                    current_page += 1
                    human_navigate_to_top(page)
                    human_delay(1.0, 2.0)
                else: break

            app_data["scrape_statistics"]["successfully_scraped_ads"] = total_ads_collected
            app_data["scrape_statistics"]["success_rate"] = f"{total_ads_collected}/{app_data['scrape_statistics']['total_attempted_ads']}"
            bundle_data["apps"].append(app_data)

        output_filename = f"raw_bundle_{run_id}.json"
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(bundle_data, f, ensure_ascii=False, indent=2)
            
        log.info(f"Lưu raw data tại: {output_filename}")
        human_wait_with_jitter(page, 1.0, 2.0) 
        context.close()

        log.info("CHUYỂN GIAO SANG GEMINI PARSER...")
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key: 
            final_json_path = process_bundle(output_filename, api_key, DEFAULT_MODEL)
            
            if final_json_path:
                log.info("CHUYỂN GIAO SANG EXCEL EXPORTER...")
                json_to_excel(final_json_path)
            else:
                log.error("Không có file final JSON để chuyển sang Excel.")

if __name__ == "__main__":
    run()