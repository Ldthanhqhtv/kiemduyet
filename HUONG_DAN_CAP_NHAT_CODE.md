# 📖 HƯỚNG DẪN CẬP NHẬT CODE LÊN STREAMLIT COMMUNITY CLOUD
> **Dự án:** Kiểm Duyệt Bài Báo AI (v2.0)  
> **Kho GitHub:** [https://github.com/Ldthanhqhtv/kiemduyet](https://github.com/Ldthanhqhtv/kiemduyet)  
> **Nền tảng chạy:** Streamlit Community Cloud

---

## ⚡ CƠ CHẾ HOẠT ĐỘNG TỰ ĐỘNG
Streamlit Community Cloud được liên kết trực tiếp với nhánh `main` của kho GitHub `Ldthanhqhtv/kiemduyet`.
* Mỗi khi bạn cập nhật file mới lên GitHub, **Streamlit Cloud sẽ tự động phát hiện, tự nạp lại code mới và khởi động lại sau khoảng 30 - 60 giây**.
* Bạn của bạn chỉ cần bấm **F5** trên trình duyệt là sẽ thấy ngay phiên bản mới mà **không cần cài đặt gì thêm**!

---

## 🚀 CÁCH 1: SỬA TRÊN MÁY TÍNH RỒI ĐẨY LÊN (KHUYÊN DÙNG)
*Phù hợp khi bạn sửa nhiều file, nhờ AI lập trình thêm tính năng mới hoặc nâng cấp hệ thống.*

### Bước 1: Chỉnh sửa code trên máy tính
Bạn mở thư mục `E:\kiem duyet bao` bằng phần mềm code (Antigravity IDE / VS Code) và tiến hành sửa code theo ý muốn (ví dụ sửa prompt, sửa giao diện `app.py`, thêm kỹ năng vào `skills/skills.json`...).

### Bước 2: Đẩy code mới lên GitHub bằng CMD
1. Mở cửa sổ **Command Prompt (CMD)** hoặc **PowerShell** trên máy tính.
2. Sao chép và chạy lần lượt các lệnh sau:
   ```cmd
   cd /d "E:\kiem duyet bao"
   git add .
   git commit -m "Cap nhat tinh nang moi v2.1"
   git push -u origin main
   ```
3. Sau khi lệnh chạy xong (báo `Everything up-to-date` hoặc hiện tiến trình tải lên 100%), Streamlit Cloud sẽ tự động làm mới ứng dụng ngay trên mạng!

---

## 🌐 CÁCH 2: SỬA TRỰC TIẾP TRÊN TRÌNH DUYỆT WEB (NHANH NHẤT)
*Phù hợp khi bạn chỉ muốn sửa nhanh 1 đoạn chữ, đổi prompt, hoặc thêm/bớt mã PIN mà không cần mở phần mềm code trên máy tính.*

### Bước 1: Mở file trên GitHub
1. Mở trình duyệt vào link: [https://github.com/Ldthanhqhtv/kiemduyet](https://github.com/Ldthanhqhtv/kiemduyet)
2. Bấm vào file bạn muốn sửa (Ví dụ: bấm vào file `app.py`).

### Bước 2: Sửa và Lưu
1. Ở góc trên bên phải nội dung file, bạn bấm vào biểu tượng **Cây bút chì ✏️** (*Edit this file*).
2. Chỉnh sửa nội dung trực tiếp trên web (Ví dụ: muốn thêm mã PIN mới `8888`, bạn chỉ cần tìm dòng `VALID_PINS = {"2711", "2706"}` và sửa thành `VALID_PINS = {"2711", "2706", "8888"}`).
3. Sau khi sửa xong, cuộn chuột xuống dưới cùng hoặc bấm nút xanh **Commit changes...** ở góc trên bên phải.
4. Chọn **Commit changes**. Xong! Streamlit Cloud sẽ tự cập nhật ngay.

---

## 📁 CÁCH 3: TẢI FILE MỚI ĐÈ LÊN WEB GITHUB (KÉO THẢ)
*Phù hợp khi bạn đã sửa file ở máy tính nhưng không muốn gõ lệnh trong CMD.*

1. Vào link: [https://github.com/Ldthanhqhtv/kiemduyet](https://github.com/Ldthanhqhtv/kiemduyet)
2. Bấm nút **`Add file`** (ở gần trên cùng) -> Chọn **`Upload files`**.
3. Kéo thả file bạn vừa sửa trên máy tính (ví dụ file `app.py` mới) thả vào khung trình duyệt.
4. Cuộn xuống dưới bấm nút xanh **`Commit changes`**.
5. Streamlit Cloud sẽ tự động nhận file mới này.

---

## 🔒 CÁC MẸO BẢO MẬT & QUẢN TRỊ QUAN TRỌNG

### 1. Cách cấp hoặc thu hồi mã PIN đăng nhập:
* Mở file `app.py`, tìm đoạn:
  ```python
  VALID_PINS: set = {"2711", "2706"}
  ```
* Muốn thêm bạn mới: Thêm mã PIN vào danh sách:
  ```python
  VALID_PINS: set = {"2711", "2706", "3110"}
  ```
* Muốn chặn ai đó: Chỉ cần xóa mã PIN của họ đi và lưu lại file.

### 2. Cài đặt API Key bí mật trên Streamlit Cloud (để cả nhóm dùng chung):
1. Mở trang web app đang chạy trên Streamlit Cloud.
2. Bấm vào biểu tượng **Settings ⚙️** (hoặc dấu 3 chấm `...` ở góc dưới/trên bên phải) -> Chọn **Settings**.
3. Chọn thẻ **Secrets**, dán cấu hình:
   ```toml
   GEMINI_API_KEY = "AIzaSy..."
   ```
4. Bấm **Save**. Tất cả người dùng có mã PIN vào app sẽ được dùng key này mà không cần nhập thủ công!

### 3. Xem nhật ký hoạt động (Logs):
Nếu ứng dụng gặp sự cố hoặc bạn muốn xem app đang chạy ra sao:
* Bấm vào chữ **`Manage app`** ở góc dưới cùng bên phải của trang web Streamlit Cloud.
* Một bảng đen chứa toàn bộ nhật ký (Logs) chi tiết của hệ thống sẽ hiện ra để bạn theo dõi.
