import pandas as pd
import json
import os
from custom_logger import log

def json_to_excel(json_filepath: str) -> str:
    """Đọc file final_result JSON và xuất ra file Excel."""
    
    # 1. Tạo thư mục crawl_results nếu chưa có
    output_dir = "crawl_results"
    os.makedirs(output_dir, exist_ok=True)
    
    log.info(f"Đang đọc dữ liệu từ file JSON: {json_filepath}")
    
    try:
        with open(json_filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        log.error(f"Không tìm thấy file JSON: {json_filepath}")
        return None

    run_id = data.get("run_id", "unknown")
    apps = data.get("apps", [])
    
    rows = []
    
    # 2. Bóc tách dữ liệu
    for app in apps:
        app_id = app.get("app_id")
        # Gộp list filters thành một chuỗi (vd: "90 Days, Impression")
        filters_applied = ", ".join(app.get("filters_applied", []))
        
        for ad in app.get("ads", []):
            gemini_data = ad.get("gemini_data")
            
            # Nếu thẻ ad này bị lỗi lúc parse, gemini_data sẽ là None -> bỏ qua
            if not gemini_data:
                continue
                
            # Tạo một dòng (row) mới
            row = {
                "app_id": app_id,
                "filters_applied": filters_applied
            }
            
            # Gộp TẤT CẢ các trường trong gemini_data vào dòng này
            row.update(gemini_data)
            
            rows.append(row)

    if not rows:
        log.warning("Không có dữ liệu quảng cáo hợp lệ nào để xuất ra Excel.")
        return None

    # 3. Tạo DataFrame và xuất ra Excel
    df = pd.DataFrame(rows)
    
    # Đặt tên file Excel theo run_id
    excel_filename = f"excel_result_{run_id}.xlsx"
    excel_filepath = os.path.join(output_dir, excel_filename)
    
    # Xuất ra file
    df.to_excel(excel_filepath, index=False)
    log.info(f"Hoàn thành Pipeline! Đã xuất file Excel thành công tại: {excel_filepath}")
    
    return excel_filepath