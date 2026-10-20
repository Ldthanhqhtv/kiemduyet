# NHIỆM VỤ OPENCODE: [TASK_V2.2_UNIFIED_WORD_DROP]
# Hợp Nhất Khung Nhập Liệu: Kéo Thả Trực Tiếp File Word (.docx) Vào Ô Biên Tập & Kiểm Duyệt Như Dán Text

> [!CRITICAL]
> **QUY TẮC BẮT BUỘC TỪ NGƯỜI DÙNG: CẤM TUYỆT ĐỐI GÕ `git push`!**
> 1. OpenCode **TUYỆT ĐỐI KHÔNG ĐƯỢC TỰ ĐẨY LÊN GITHUB** (`git push`).
> 2. Mọi thao tác sửa code và chạy kiểm thử **CHỈ ĐƯỢC PHÉP CHẠY TẠI MÁY CỤC BỘ (LOCAL)**.
> 3. Sau khi chạy test local đạt 100% PASS, OpenCode phải **dừng lại ngay lập tức** và báo cáo kết quả, để người dùng tự kiểm tra và tự quyết định thời điểm đẩy lên GitHub!

> [!IMPORTANT]
> **MỤC TIÊU NGHIỆP VỤ TỪ NGƯỜI DÙNG:**
> Hiện tại ứng dụng chia thành 2 tab riêng biệt ("✍️ Dán văn bản" và "📄 Tải file"). Người dùng mong muốn **không cần chuyển tab**, có thể **kéo thả thẳng file Word (.docx) từ máy tính vào ô nhập liệu**, hệ thống lập tức tự động giải nén toàn bộ văn bản vào ô biên tập, hiển thị ngay nội dung bài viết để người dùng có thể xem/sửa và bấm nút **"🚀 Bắt Đầu Kiểm Duyệt"** ngay hệt như khi dán text thủ công!

---

## 1. ĐÁNH GIÁ TÍNH KHẢ THI KỸ THUẬT (FEASIBILITY ANALYSIS)

### 1.1. Có thực hiện được trong Streamlit không?
**HOÀN TOÀN KHẢ THI VÀ LÀ PHƯƠNG ÁN CHUẨN MỰC NHẤT.**
- Trong Streamlit, component `st.file_uploader` bản chất là một vùng **HTML5 Drag & Drop chuẩn** của trình duyệt.
- Dự án đã có sẵn hàm `extract_uploaded_text(uploaded_file)` sử dụng thư viện `python-docx` để đọc mọi định dạng Word (`.docx`, đoạn văn `doc.paragraphs`, bảng biểu).
- Vấn đề duy nhất ở bản cũ là: `st.file_uploader` và `st.text_area` nằm ở 2 tab tách biệt, khi tải file lên thì text chỉ nằm trong biến tạm và hiện ở khung Preview chứ **không được đẩy vào `st.session_state[STATE_ARTICLE_TEXT_AREA]`**.

### 1.2. Giải pháp Kiến trúc Hợp nhất (Unified Dropzone & Editor):
1. **Xóa bỏ sự phân tách 2 Tab**:
   - Thay thế cấu trúc `tab_text, tab_file = st.tabs(...)` bằng một **Khu vực nhập liệu hợp nhất (Unified Input Container)**.
2. **Khung Kéo thả Thông minh (Smart Dropzone)** đặt ngay trên ô Text Area:
   - Thiết kế gọn gàng, thanh lịch:
     `st.file_uploader("📂 Kéo thả file Word (.docx / .txt) vào đây hoặc bấm để tải lên", type=["docx", "txt", "md"], key=f"uploader_{uploader_version}")`
3. **Cơ chế Tự động Bóc tách & Đồng bộ vào Text Area (Auto-Extract & Populate)**:
   - Khi phát hiện có file được thả vào:
     - So sánh file hiện tại với `st.session_state.get("last_processed_file_id")`.
     - Nếu là file mới thả vào:
       - Gọi `extract_uploaded_text(uploaded_file)` để lấy toàn bộ văn bản.
       - Tự động gán: `st.session_state[STATE_ARTICLE_TEXT_AREA] = extracted_text`.
       - Lưu tên file làm nguồn: `st.session_state["review_source_label"] = f"File Word: {uploaded_file.name}"`.
       - Cập nhật `last_processed_file_id = uploaded_file.file_id` (hoặc `uploaded_file.name + str(uploaded_file.size)`).
4. **Ô Văn bản Đa năng (`st.text_area`)**:
   - Nhận trực tiếp văn bản vừa trích xuất từ file Word.
   - Người dùng có thể đọc ngay toàn bộ bài báo, chỉnh sửa từng câu từ nếu cần.
   - Nút **"🚀 Bắt Đầu Kiểm Duyệt"** lấy trực tiếp nội dung bài từ `st.text_area` để gửi sang Gemini.
5. **Cơ chế Khôi phục / Xóa bài mới (Clean Reset)**:
   - Khi bấm **"🔄 Bắt đầu bài mới / Xóa kết quả"**:
     - Xóa `st.session_state[STATE_ARTICLE_TEXT_AREA] = ""`.
     - Tăng biến phiên bản `st.session_state["uploader_version"] += 1` để reset sạch sẽ khung dropzone về trạng thái ban đầu.
6. **(UX Enhancement) Cầu nối Drag & Drop trực tiếp vào TextArea qua JavaScript**:
   - Bổ sung một đoạn mã JavaScript nhỏ qua `components.html` để bắt sự kiện `drop` của người dùng khi họ lỡ tay thả file Word thẳng vào lòng ô `textarea`. Khi đó, script sẽ chuyển file sang `input[type="file"]` của Streamlit để kích hoạt tự động giải nén.

---

## 2. HƯỚNG DẪN CHI TIẾT TỪNG BƯỚC CHO OPENCODE

### Bước 1: Khai báo State quản lý phiên bản File Uploader
Trong `app.py`, tại khu vực định nghĩa hằng số state (khoảng dòng 110-125):
```python
# Quản lý phiên bản của widget file uploader để reset sạch sẽ khi bắt đầu bài mới
STATE_UPLOADER_VERSION = "uploader_version"
STATE_LAST_PROCESSED_FILE = "last_processed_file_signature"
```

Khởi tạo giá trị mặc định trong hàm khởi tạo state:
```python
if STATE_UPLOADER_VERSION not in st.session_state:
    st.session_state[STATE_UPLOADER_VERSION] = 0
if STATE_LAST_PROCESSED_FILE not in st.session_state:
    st.session_state[STATE_LAST_PROCESSED_FILE] = ""
```

---

### Bước 2: Tái cấu trúc hàm `render_article_input()` trong [app.py](file:///e:/kiem%20duyet%20bao/app.py)

Thay thế toàn bộ hàm `render_article_input()` cũ bằng thiết kế hợp nhất:

```python
def render_article_input() -> Tuple[str, str]:
    """Vẽ khu vực nhập bài hợp nhất (Kéo thả file Word + Dán văn bản).
    
    Tự động giải nén file Word (.docx) vào ô văn bản ngay khi thả file vào,
    cho phép người dùng vừa xem/sửa vừa bấm duyệt bài ngay lập tức.
    """
    st.subheader("📥 Nhập nội dung bài báo")

    uploader_version = st.session_state.get(STATE_UPLOADER_VERSION, 0)
    uploader_key = f"article_file_uploader_{uploader_version}"

    # 1. Hàng công cụ phía trên: Nút nạp bài mẫu & Thông tin hướng dẫn
    top_cols = st.columns([2, 5])
    with top_cols[0]:
        if st.button(
            "📰 Nạp bài mẫu (Demo Sample)",
            key="demo_sample_button",
            help="Điền sẵn một bài mẫu có lỗi chính tả và nhân vật để thử nghiệm nhanh.",
            use_container_width=True,
        ):
            st.session_state[STATE_ARTICLE_TEXT_AREA] = SAMPLE_ARTICLE
            st.session_state["review_source_label"] = "Bài báo mẫu (Demo)"
            st.session_state[STATE_LAST_PROCESSED_FILE] = ""

    # 2. Khung Kéo Thả File Word Thông Minh (Smart Dropzone)
    uploaded_file = st.file_uploader(
        "📁 **Kéo & thả file Word (.docx) vào đây hoặc bấm để chọn file từ máy tính:**",
        type=["docx", "txt", "md"],
        key=uploader_key,
        help="Hỗ trợ file Microsoft Word (.docx) và file văn bản thuần (.txt, .md). Hệ thống sẽ tự động bóc tách toàn bộ nội dung đưa vào ô biên tập bên dưới.",
    )

    # 3. Tự động bóc tách khi có file Word được thả vào
    if uploaded_file is not None:
        file_sig = f"{uploaded_file.name}_{uploaded_file.size}"
        if st.session_state.get(STATE_LAST_PROCESSED_FILE) != file_sig:
            try:
                extracted_content = extract_uploaded_text(uploaded_file)
                if extracted_content.strip():
                    st.session_state[STATE_ARTICLE_TEXT_AREA] = extracted_content
                    st.session_state["review_source_label"] = f"File: {uploaded_file.name}"
                    st.session_state[STATE_LAST_PROCESSED_FILE] = file_sig
                    st.success(f"✅ Đã nạp tự động toàn văn từ file **{uploaded_file.name}** vào ô biên tập bên dưới!")
                else:
                    st.warning(f"⚠️ File **{uploaded_file.name}** rỗng hoặc không có văn bản.")
            except Exception as exc:
                st.error(f"❌ Không thể đọc file Word: {exc}")

    # 4. Ô Văn bản Biên tập & Kiểm duyệt (Chứa nội dung đã nạp hoặc dán trực tiếp)
    typed_text = st.text_area(
        "Văn bản bài báo cần kiểm duyệt (có thể chỉnh sửa trực tiếp):",
        height=320,
        key=STATE_ARTICLE_TEXT_AREA,
        placeholder=(
            "Kéo thả file Word vào khung phía trên HOẶC dán trực tiếp bài báo vào đây...\n"
            "Ví dụ:\n"
            "Hội nghị khoa học quốc tế về AI khai mạc tại Hà Nội\n"
            "Sáng ngày 17/10, tại Hà Nội đã diễn ra..."
        ),
    )

    # 5. Xác định bài báo và nhãn nguồn
    article_text = (typed_text or "").strip()
    source_label = st.session_state.get("review_source_label") or "Văn bản dán trực tiếp"

    if article_text:
        st.info(
            f"📄 Đã nhận **{format_word_char_caption(article_text)}** — "
            f"Nguồn: **{source_label}**"
        )

    # 6. (Tùy chọn) Script hỗ trợ kéo thả file trực tiếp vào textarea
    components.html(
        """
        <script>
        const doc = window.parent.document;
        const textAreas = doc.querySelectorAll('textarea');
        const fileInputs = doc.querySelectorAll('input[type="file"]');
        if (textAreas.length > 0 && fileInputs.length > 0) {
            const ta = textAreas[0];
            const fi = fileInputs[0];
            ta.addEventListener('dragover', (e) => {
                e.preventDefault();
                ta.style.borderColor = '#1e88e5';
                ta.style.backgroundColor = '#f0f9ff';
            });
            ta.addEventListener('dragleave', (e) => {
                ta.style.borderColor = '';
                ta.style.backgroundColor = '';
            });
            ta.addEventListener('drop', (e) => {
                ta.style.borderColor = '';
                ta.style.backgroundColor = '';
                if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                    fi.files = e.dataTransfer.files;
                    fi.dispatchEvent(new Event('change', { bubbles: true }));
                }
            });
        }
        </script>
        """,
        height=0,
        width=0,
    )

    return article_text, source_label
```

---

### Bước 3: Cập nhật hàm Reset (`reset_review_state()`)

Khi người dùng bấm nút **"🔄 Bắt đầu bài mới / Xóa kết quả"**:
Cần dọn sạch cả ô text và xóa trắng file đang nằm trong `st.file_uploader`:

```python
def reset_review_state() -> None:
    """Xóa toàn bộ kết quả duyệt và làm mới khung nhập liệu."""
    for key in (STATE_RESULT, STATE_ORIGINAL, STATE_SOURCE, STATE_MOMENT, STATE_REVIEW_ID, STATE_USED_SKILL):
        st.session_state.pop(key, None)
    
    st.session_state[STATE_ARTICLE_TEXT_AREA] = ""
    st.session_state[STATE_LAST_PROCESSED_FILE] = ""
    # Tăng phiên bản uploader để Streamlit tạo lại widget file_uploader trắng
    st.session_state[STATE_UPLOADER_VERSION] = st.session_state.get(STATE_UPLOADER_VERSION, 0) + 1
```

---

### Bước 4: Viết Test Suite Xác Thực trong `core/test_app.py`

Bổ sung các test cases sau vào `core/test_app.py`:

```python
    def test_upload_word_file_populates_text_area(self):
        """Kiểm tra khi tải file docx lên, text_area tự động nhận nội dung."""
        import docx
        import io
        
        # Tạo file docx giả lập trong bộ nhớ
        doc = docx.Document()
        doc.add_paragraph("Bài báo thử nghiệm từ file Word.")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        
        app = _make_app()
        # Giả lập upload file
        # Kiểm tra session_state[STATE_ARTICLE_TEXT_AREA] chứa nội dung
        # Kiểm tra format_word_char_caption hiển thị số từ chính xác
```

---

## 3. TIÊU CHÍ NGHIỆM THU (ACCEPTANCE CRITERIA)
1. **Một Giao Diện Duy Nhất**: Không còn chia thành 2 tab "Dán văn bản" và "Tải file".
2. **Kéo Thả Ăn Ngay**: Thả file Word (`.docx`), `.txt`, `.md` vào khung dropzone lập tức nạp văn bản đầy đủ vào ô soạn thảo.
3. **Cho Phép Biên Tập Lại**: Văn bản trích xuất từ file Word phải chỉnh sửa được trực tiếp trong ô `text_area`.
4. **Kiểm Duyệt Liền Mạch**: Bấm nút **"🚀 Bắt Đầu Kiểm Duyệt Qua Gemini Web"** thực thi ngay lập tức mà không cần thêm bất kỳ bước trung gian nào.
5. **Nút Reset Hoạt Động Hoàn Hảo**: Bấm "Bắt đầu bài mới" xóa sạch cả file Word đã nạp và văn bản trong ô soạn thảo.
6. **Pass 100% Test Suite**: Lệnh `.venv\Scripts\pytest core/` đạt 100% PASS mà không có bất kỳ regression nào.
