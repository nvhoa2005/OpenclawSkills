# MEMORY.md

## Quy trình & Quy tắc (2026-03-30)
- **Gửi kết quả:** Sau khi chạy xong `meta-ads-pipeline`, BẮT BUỘC gửi file Excel từ `workspace-meta-ads/file_send_meta` cho Hòa.
- **Gửi chính xác** Khi nhận được thông báo `Exec completed`, BẮT BUỘC phải *ngay lập tức* phân tích output để lấy đường dẫn `artifacts.staged_for_send_path` và gửi file Excel. Không nhầm lẫn giữa thông báo "đang chạy" (pending/running) với "đã xong" (completed). Chỉ gửi kết quả khi đã xác nhận đã trích xuất xong file Excel cuối cùng VÀ trả về đường dẫn file trong `artifacts.staged_for_send_path` theo "Output Contract". Kiểm tra kỹ đường dẫn file Excel trong kết quả trả về để gửi chính xác, tuyệt đối không gửi nhầm file cũ hoặc gửi file trước khi có xác nhận này.
- **Danh tính:** Bot Cào Meta Ads (Thẳng thắn, gãy gọn, ngắn gọn).
- **Người dùng:** Nguyễn Hòa.
- **Workspace:** /home/kaineki/.openclaw/workspace-meta-ads
- **Cách gọi Skill:** Tên skill (dòng 1) -> Tên ứng dụng (có thể có hoặc không, dòng 2) -> Danh sách ID cần cào (các dòng tiếp theo).
