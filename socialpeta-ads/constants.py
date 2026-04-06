# --- URL & TIMEOUT ---
TARGET_URL = "https://socialpeta.com/modules/creative/display-ads"
DEFAULT_TIMEOUT = 20000 
LONG_TIMEOUT = 60000   

# --- VIEWPORT SETTINGS ---
VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080

# --- GEMINI MODELS ---
DEFAULT_MODEL = "gemini-2.5-flash-lite"
VIDEO_MODEL = "gemini-2.5-flash"

class Selectors:
    # Chọn app
    CHOOSE_APP = "span.font-bold"

    # Filter Platform (Nền tảng)
    PLATFORM_MORE_BTN = "#filter_platform"
    PLATFORM_YOUTUBE_CHECKBOX = 'label.ant-checkbox-wrapper:has(input[value="youtube"])'
    PLATFORM_OK_BTN = 'button.ant-btn-primary:has-text("OK")'

    # Thẻ quảng cáo
    AD_CARD = "div.shadow-common-light"
    SKELETON_LOADER = ".ant-skeleton"
    MEDIA_CONTENT = "img, video"
    ANY_TEXT_DIV = "div"
    
    # Popup chi tiết
    MODAL_CONTENT = ".ant-modal-content"
    CLOSE_BTN_ROLE = "button"
    CLOSE_BTN_NAME = "Close"
    
    # Thanh tìm kiếm & Xóa bộ lọc
    CLEAR_BTN = "button.ant-btn-color-dangerous"
    SEARCH_INPUT = "#rc_select_1"
    
    # Dropdown (Nút ...)
    MORE_DROPDOWN_BTN = ".ant-dropdown-trigger"
    MORE_ICON = ".zf-icon-more-dot"
    DROPDOWN_MENU = ".ant-dropdown-menu"
    
    # Phân trang (Pagination)
    PAGE_BTN_TEMPLATE = 'li[title="{}"]'
    NEXT_PAGE_BTN = "li.ant-pagination-next:not(.ant-pagination-disabled)"

# --- DANH SÁCH BỘ LỌC HỢP LỆ (VALIDATION) ---
TIME_FILTERS = ["7 Days", "30 Days", "90 Days", "1 Year"]
SORT_FILTERS = ["Latest Creatives", "Last Seen", "Impression"]
DROPDOWN_SORTS = ["Related Ads", "Popularity", "Like", "Comment", "Share"]

# --- GEMINI PROMPT ---
GEMINI_PROMPT_TEMPLATE = """
Hãy trích xuất thông tin quảng cáo từ đoạn mã HTML sau.
Nếu trường nào không có dữ liệu, hãy để null. Không bịa đặt dữ liệu.
Lưu ý nếu không có thì để null, top 1% creative và top 10% creative nếu không có thì là false, headline thường có hiệu ứng khi hover vào, không được phép trả lời thừa ngoài yêu cầu

HTML:
{html}
"""