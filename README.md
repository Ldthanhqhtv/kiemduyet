---
title: Kiểm Duyệt Bài Báo AI
emoji: 📰
colorFrom: blue
colorTo: indigo
sdk: streamlit
sdk_version: 1.66.0
python_version: "3.12"
app_file: app.py
pinned: false
license: mit
---

# 📰 Kiểm Duyệt Bài Báo AI

Công cụ hỗ trợ ban biên tập: **kiểm tra chính tả**, **kiểm chứng sự kiện/nhân vật bằng Google Search**
và **xuất báo cáo Word** sẵn sàng trình duyệt.

Ứng dụng là giao diện web Streamlit (chạy được cả trên Hugging Face Spaces và trên máy local),
dùng lõi AI đã hoàn thành ở Task 1 (`core/reviewer.py`) và module xuất Word ở Task 2
(`core/docx_exporter.py`).

---

## ✨ Tính năng

| Khu vực | Mô tả |
|---|---|
| Nhập bài | Dán trực tiếp văn bản **hoặc** tải file `.docx` / `.txt` / `.md` gốc |
| Duyệt bài | Gọi `gemini-2.5-flash` kèm **Google Search grounding** để tra cứu nguồn thật |
| Lỗi chính tả | Bảng `Lỗi sai / Vị trí / Gợi ý sửa / Lý do` |
| Kiểm chứng | Bảng `Thông tin cần kiểm chứng / Kết quả xác minh / Trích dẫn nguồn / Độ tin cậy` |
| Nguồn tham khảo | Danh sách URL thật do Google Search trả về + các truy vấn đã dùng |
| Kết quả | Toàn văn bài báo đã biên tập + nút tải báo cáo `.docx` |

Cấu trúc file Word xuất ra: header tòa soạn → bảng lỗi chính tả → bảng kiểm chứng kèm URL →
phụ lục nguồn Google Search → ngắt trang → **toàn văn bài báo đã biên tập**.

---

## 🚀 Chạy trên máy local

```powershell
# 1. Cài thư viện (dùng đúng python trong venv của dự án)
& "E:\kiem duyet bao\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# 2. Chạy giao diện
& "E:\kiem duyet bao\.venv\Scripts\python.exe" -m streamlit run app.py
```

Trình duyệt sẽ tự mở tại `http://localhost:8501`.

## 🔑 Cấu hình API key

Có 2 cách:

1. Nhập trực tiếp vào ô `GEMINI_API_KEY` ở **sidebar** (key chỉ dùng trong phiên, không lưu lại).
2. Tạo file `.env` ở thư mục gốc dự án:

   ```
   GEMINI_API_KEY=dan_key_that_vao_day
   ```

   hoặc đặt biến môi trường `GEMINI_API_KEY`.

Lấy API key miễn phí tại <https://aistudio.google.com/apikey>.

> Trên Hugging Face Spaces, vào **Settings → Variables and secrets** và thêm secret
> `GEMINI_API_KEY` để ứng dụng tự nhận key.

---

## 📁 Cấu trúc dự án liên quan

```
app.py                  # Giao diện Streamlit (file này)
requirements.txt        # Thư viện cho HF Spaces
README.md               # Tài liệu + header YAML của HF Spaces
core/reviewer.py        # Task 1: lõi AI kiểm duyệt (Gemini + Google Search)
core/docx_exporter.py   # Task 2: xuất báo cáo Word
```

## ⚠️ Lưu ý

- Bài báo quá dài (vượt giới hạn ký tự của lõi reviewer) cần được chia nhỏ trước khi duyệt.
- Kết quả AI chỉ mang tính hỗ trợ; biên tập viên vẫn là người quyết định cuối cùng.
