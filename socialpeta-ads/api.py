import uuid
import os
import json
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from crawler import run as run_crawler
from constants import TIME_FILTERS, SORT_FILTERS, DROPDOWN_SORTS
from custom_logger import log

app = FastAPI(title="SocialPeta Crawler API")

TASKS_DB = {}

class CrawlRequest(BaseModel):
    app_id: str = Field(..., description="Danh sách App ID, cách nhau bởi dấu xuống dòng (\\n)")
    time_val: str = Field(default="90 Days")
    sort_val: str = Field(default="Impression")
    max_ads: int = Field(default=100)
    start_page: int = Field(default=1)

def background_crawl_task(task_id: str, req: CrawlRequest):
    try:
        log.info(f"Task {task_id} bắt đầu chạy ngầm...")
        TASKS_DB[task_id]["status"] = "processing"
        
        raw_app_ids = req.app_id.strip().split('\n')
        app_ids = [aid.strip() for aid in raw_app_ids if aid.strip()]
        
        time_val = req.time_val if req.time_val in TIME_FILTERS else "90 Days"
        sort_val = req.sort_val if req.sort_val in SORT_FILTERS + DROPDOWN_SORTS else "Impression"
        
        tasks_list = []
        for aid in app_ids:
            tasks_list.append({
                "app_id": aid,
                "time_val": time_val,
                "sort_val": sort_val,
                "max_ads": req.max_ads,
                "start_page": req.start_page
            })
            
        run_crawler(api_tasks=tasks_list, custom_run_id=task_id)
        
        TASKS_DB[task_id]["status"] = "completed"
        TASKS_DB[task_id]["result_file"] = f"final_result_{task_id}.json"
        log.info(f"Task {task_id} đã hoàn thành toàn bộ vòng đời!")
        
    except Exception as e:
        TASKS_DB[task_id]["status"] = "failed"
        TASKS_DB[task_id]["error"] = str(e)
        log.error(f"Task {task_id} bị lỗi: {e}")

# --- API ENDPOINTS ---

@app.post("/api/v1/crawl")
async def start_crawl(request: CrawlRequest, background_tasks: BackgroundTasks):
    """
    API nhận yêu cầu, tạo Task ID và ném việc cho chạy ngầm.
    """
    # Tạo mã ID duy nhất cho mỗi lần gọi
    task_id = datetime.now().strftime("crawl_%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:6]
    
    TASKS_DB[task_id] = {
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "total_apps": len([aid for aid in request.app_id.split('\n') if aid.strip()])
    }
    
    # Ném hàm background_crawl_task ra chạy nền
    background_tasks.add_task(background_crawl_task, task_id, request)
    
    return {
        "task_id": task_id, 
        "status": "pending", 
        "message": "Hệ thống đã ghi nhận yêu cầu và đang khởi động luồng lấy dữ liệu."
    }

@app.get("/api/v1/status/{task_id}")
async def get_status(task_id: str):
    """
    API kiểm tra tiến độ và lấy kết quả trả về.
    """
    if task_id not in TASKS_DB:
        raise HTTPException(status_code=404, detail="Không tìm thấy task_id này.")
    
    task_info = TASKS_DB[task_id]
    
    # Nếu trạng thái đã xong, đọc file final_result do Gemini tạo ra nhồi vào response
    if task_info["status"] == "completed":
        result_file = task_info.get("result_file")
        if result_file and os.path.exists(result_file):
            with open(result_file, 'r', encoding='utf-8') as f:
                task_info["data"] = json.load(f)
        else:
            task_info["status"] = "failed"
            task_info["error"] = "Không tìm thấy file kết quả đầu ra."
            
    return task_info

# uvicorn api:app --reload