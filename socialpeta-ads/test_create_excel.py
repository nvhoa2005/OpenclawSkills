from export_excel import json_to_excel
from custom_logger import log

final_json_path = "final_result_crawl_20260405_103000.json"
            
if final_json_path:
    log.info("CHUYỂN GIAO SANG EXCEL EXPORTER...")
    json_to_excel(final_json_path)
else:
    log.error("Không có file final JSON để chuyển sang Excel.")