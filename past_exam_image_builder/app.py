import json
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageDraw, ImageTk

from library import LibraryEntry, OverlayField, PastExamLibrary, build_source_id
from pdf_tools import PdfDocument


APP_TITLE = "기출이미지생성기"


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class ImageCanvas(ttk.Frame):
    def __init__(self, master, selection_callback=None):
        super().__init__(master)
        self.canvas = tk.Canvas(self, bg="#d8d8d8", highlightthickness=0)
        self.xbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.ybar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.xbar.set, yscrollcommand=self.ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.ybar.grid(row=0, column=1, sticky="ns")
        self.xbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.image = None
        self.photo = None
        self.selection = None
        self.selection_id = None
        self.start = None
        self.selection_callback = selection_callback
        self.overlays = []
        self.canvas.bind("<ButtonPress-1>", self._start_selection)
        self.canvas.bind("<B1-Motion>", self._move_selection)
        self.canvas.bind("<ButtonRelease-1>", self._finish_selection)

    def show_image(self, image):
        self.image = image.copy()
        self.photo = ImageTk.PhotoImage(self.image)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, self.image.width, self.image.height))
        self.selection = None
        self.selection_id = None
        self.draw_overlays()

    def draw_overlays(self):
        for overlay in self.overlays:
            box = overlay.box
            self.canvas.create_rectangle(*box, outline="#d12f2f", width=2)
            self.canvas.create_text(
                box[0] + 4,
                box[1] + 4,
                text=overlay.name,
                fill="#d12f2f",
                anchor="nw",
                font=("Malgun Gothic", 10, "bold"),
            )

    def _canvas_point(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _start_selection(self, event):
        if not self.image:
            return
        self.start = self._canvas_point(event)
        if self.selection_id:
            self.canvas.delete(self.selection_id)
        self.selection_id = self.canvas.create_rectangle(
            self.start[0], self.start[1], self.start[0], self.start[1],
            outline="#1976d2", width=2,
        )

    def _move_selection(self, event):
        if not self.start or not self.selection_id:
            return
        x, y = self._canvas_point(event)
        self.canvas.coords(self.selection_id, self.start[0], self.start[1], x, y)

    def _finish_selection(self, event):
        if not self.start:
            return
        x, y = self._canvas_point(event)
        x0, x1 = sorted((self.start[0], x))
        y0, y1 = sorted((self.start[1], y))
        if self.image:
            x0 = max(0, min(self.image.width, x0))
            x1 = max(0, min(self.image.width, x1))
            y0 = max(0, min(self.image.height, y0))
            y1 = max(0, min(self.image.height, y1))
        self.selection = [int(x0), int(y0), int(x1), int(y1)]
        self.start = None
        if self.selection_callback and x1 - x0 >= 4 and y1 - y0 >= 4:
            self.selection_callback(self.selection)


class RegisterTab(ttk.Frame):
    def __init__(self, master, library, refresh_callback):
        super().__init__(master)
        self.library = library
        self.refresh_callback = refresh_callback
        self.pdf = None
        self.pdf_path = None
        self.page_index = 0
        self.page_image = None
        self.crop_image = None
        self.crop_box = None
        self.mode = "page"
        self.overlays = []
        self.candidates = []
        self.candidate_index = -1
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="PDF 열기", command=self.open_pdf).pack(side="left")
        ttk.Button(toolbar, text="◀ 이전", command=lambda: self.move_page(-1)).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="다음 ▶", command=lambda: self.move_page(1)).pack(side="left")
        self.page_label = ttk.Label(toolbar, text="PDF를 선택하세요")
        self.page_label.pack(side="left", padx=12)
        ttk.Button(toolbar, text="자동 그림 후보", command=self.auto_candidate).pack(side="left", padx=(12, 0))
        ttk.Button(toolbar, text="선택 영역 미리보기", command=self.preview_crop).pack(side="left", padx=(12, 0))
        ttk.Button(toolbar, text="페이지로 돌아가기", command=self.show_page).pack(side="left")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        self.canvas = ImageCanvas(body, self.selection_changed)
        body.add(self.canvas, weight=4)

        side = ttk.Frame(body, padding=10)
        body.add(side, weight=1)
        self.fields = {}
        defaults = [
            ("학교명", "대방중학교"),
            ("연도", "2024"),
            ("학년", "3"),
            ("학기", "1"),
            ("시험구분", "기말고사"),
            ("문제번호", "24"),
        ]
        for row, (label, default) in enumerate(defaults):
            ttk.Label(side, text=label).grid(row=row, column=0, sticky="w", pady=3)
            value = tk.StringVar(value=default)
            ttk.Entry(side, textvariable=value, width=22).grid(row=row, column=1, sticky="ew", pady=3)
            self.fields[label] = value

        ttk.Separator(side).grid(row=7, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(side, text="숫자 교체 영역", font=("Malgun Gothic", 10, "bold")).grid(
            row=8, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            side,
            text="그림 미리보기 상태에서 기존 숫자 영역을 드래그한 뒤 아래 버튼을 누르세요.",
            wraplength=240,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(3, 8))
        ttk.Button(side, text="선택 영역을 숫자 변수로 추가", command=self.add_overlay).grid(
            row=10, column=0, columnspan=2, sticky="ew"
        )
        ttk.Button(side, text="마지막 숫자 영역 삭제", command=self.remove_overlay).grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )
        self.overlay_list = tk.Listbox(side, height=8)
        self.overlay_list.grid(row=12, column=0, columnspan=2, sticky="nsew", pady=8)
        ttk.Button(side, text="문제 이미지 등록", command=self.save_entry).grid(
            row=13, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        side.columnconfigure(1, weight=1)
        side.rowconfigure(12, weight=1)

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        if self.pdf:
            self.pdf.close()
        self.pdf_path = path
        self.pdf = PdfDocument(path, zoom=2.0)
        self.page_index = 0
        self.show_page()

    def show_page(self):
        if not self.pdf:
            return
        self.mode = "page"
        self.page_image = self.pdf.render_page(self.page_index)
        self.canvas.overlays = []
        self.canvas.show_image(self.page_image)
        self.page_label.configure(text=f"{self.page_index + 1} / {self.pdf.page_count} 페이지")

    def move_page(self, delta):
        if not self.pdf:
            return
        self.page_index = max(0, min(self.pdf.page_count - 1, self.page_index + delta))
        self.crop_box = None
        self.overlays = []
        self.show_page()

    def selection_changed(self, box):
        if self.mode == "page":
            self.crop_box = box

    def preview_crop(self):
        if not self.page_image or not self.crop_box:
            messagebox.showinfo(APP_TITLE, "페이지에서 그림 영역을 먼저 드래그하세요.")
            return
        self.crop_image = self.page_image.crop(tuple(self.crop_box))
        self.mode = "crop"
        self.canvas.overlays = self.overlays
        self.canvas.show_image(self.crop_image)

    def auto_candidate(self):
        if not self.pdf:
            return
        self.candidates = self.pdf.image_candidates(self.page_index)
        if not self.candidates:
            messagebox.showinfo(APP_TITLE, "독립 이미지 후보를 찾지 못했습니다. 직접 드래그해 주세요.")
            return
        self.candidate_index = (self.candidate_index + 1) % len(self.candidates)
        self.crop_box = self.candidates[self.candidate_index]
        self.canvas.selection = self.crop_box
        if self.canvas.selection_id:
            self.canvas.delete(self.canvas.selection_id)
        self.canvas.selection_id = self.canvas.create_rectangle(
            *self.crop_box, outline="#1976d2", width=3
        )
        self.page_label.configure(
            text=f"{self.page_index + 1} / {self.pdf.page_count} 페이지 · 후보 {self.candidate_index + 1}/{len(self.candidates)}"
        )

    def add_overlay(self):
        if self.mode != "crop" or not self.canvas.selection:
            messagebox.showinfo(APP_TITLE, "그림 미리보기에서 숫자 영역을 드래그하세요.")
            return
        name = simpledialog.askstring(APP_TITLE, "변수 이름 (예: right_root, angle)")
        if not name:
            return
        original = simpledialog.askstring(APP_TITLE, "현재 표시된 값", initialvalue="") or ""
        font_size = simpledialog.askinteger(APP_TITLE, "출력 글자 크기", initialvalue=28, minvalue=8, maxvalue=100)
        overlay = OverlayField(
            name=name.strip(),
            original=original.strip(),
            box=list(self.canvas.selection),
            font_size=font_size or 28,
        )
        self.overlays.append(overlay)
        self.canvas.overlays = self.overlays
        self.canvas.show_image(self.crop_image)
        self.refresh_overlay_list()

    def remove_overlay(self):
        if self.overlays:
            self.overlays.pop()
            self.canvas.overlays = self.overlays
            self.canvas.show_image(self.crop_image)
            self.refresh_overlay_list()

    def refresh_overlay_list(self):
        self.overlay_list.delete(0, "end")
        for overlay in self.overlays:
            self.overlay_list.insert("end", f"{overlay.name} = {overlay.original}  {overlay.box}")

    def save_entry(self):
        if not self.crop_image or not self.crop_box or not self.pdf:
            messagebox.showinfo(APP_TITLE, "PDF에서 그림 영역을 선택하고 미리보기를 만드세요.")
            return
        try:
            source_id = build_source_id(
                self.fields["학교명"].get(),
                self.fields["연도"].get(),
                self.fields["학년"].get(),
                self.fields["학기"].get(),
                self.fields["시험구분"].get(),
                self.fields["문제번호"].get(),
            )
            entry = LibraryEntry(
                source_id=source_id,
                school=self.fields["학교명"].get().strip(),
                year=int(self.fields["연도"].get()),
                grade=int(self.fields["학년"].get()),
                semester=int(self.fields["학기"].get()),
                exam_type=self.fields["시험구분"].get().strip(),
                problem_number=int(self.fields["문제번호"].get()),
                page_number=self.page_index + 1,
                crop_box_pdf=self.pdf.pixel_box_to_pdf(self.crop_box),
                overlays=self.overlays,
            )
            target = self.library.save_entry(entry, self.crop_image, self.pdf_path)
        except Exception as error:
            messagebox.showerror(APP_TITLE, str(error))
            return
        self.refresh_callback()
        messagebox.showinfo(APP_TITLE, f"등록 완료\n{source_id}\n{target}")


class GenerateTab(ttk.Frame):
    def __init__(self, master, library):
        super().__init__(master, padding=10)
        self.library = library
        self.current_entry = None
        self.preview_photo = None
        self.value_vars = {}
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="등록 문제").pack(side="left")
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(top, textvariable=self.source_var, state="readonly", width=58)
        self.source_combo.pack(side="left", padx=8)
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_selected())
        ttk.Button(top, text="목록 새로고침", command=self.refresh).pack(side="left")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.preview = ttk.Label(body, anchor="center")
        body.add(self.preview, weight=3)
        self.form = ttk.Frame(body, padding=10)
        body.add(self.form, weight=1)
        self.info_label = ttk.Label(self.form, text="등록 문제를 선택하세요.", wraplength=280)
        self.info_label.pack(fill="x")
        self.values_frame = ttk.Frame(self.form)
        self.values_frame.pack(fill="x", pady=12)
        ttk.Button(self.form, text="새 PNG 생성", command=self.generate).pack(fill="x", pady=(8, 0))
        self.refresh()

    def refresh(self):
        items = self.library.load_index()
        values = [item["source_id"] for item in items]
        self.source_combo["values"] = values
        if values and self.source_var.get() not in values:
            self.source_var.set(values[0])
            self.load_selected()

    def load_selected(self):
        source_id = self.source_var.get()
        if not source_id:
            return
        self.current_entry = self.library.load_entry(source_id)
        image = Image.open(self.library.original_path(source_id)).convert("RGB")
        image.thumbnail((760, 620))
        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview.configure(image=self.preview_photo)
        self.info_label.configure(
            text=(
                f"{source_id}\n"
                f"{self.current_entry.school} {self.current_entry.year} "
                f"{self.current_entry.grade}학년 {self.current_entry.semester}학기 "
                f"{self.current_entry.exam_type} {self.current_entry.problem_number}번"
            )
        )
        for child in self.values_frame.winfo_children():
            child.destroy()
        self.value_vars = {}
        for row, overlay in enumerate(self.current_entry.overlays):
            ttk.Label(self.values_frame, text=overlay.name).grid(row=row, column=0, sticky="w", pady=3)
            variable = tk.StringVar(value=overlay.original)
            ttk.Entry(self.values_frame, textvariable=variable, width=18).grid(
                row=row, column=1, sticky="ew", padx=(8, 0), pady=3
            )
            self.value_vars[overlay.name] = variable
        if not self.current_entry.overlays:
            ttk.Label(
                self.values_frame,
                text="숫자 교체 영역이 없습니다. 원본 그림 복사본만 생성됩니다.",
                wraplength=260,
            ).pack(fill="x")

    def generate(self):
        if not self.current_entry:
            return
        output = filedialog.asksaveasfilename(
            defaultextension=".png",
            initialfile=f"{self.current_entry.source_id}_변형.png",
            filetypes=[("PNG", "*.png")],
        )
        if not output:
            return
        values = {name: variable.get() for name, variable in self.value_vars.items()}
        try:
            path = self.library.render(self.current_entry.source_id, values, output)
            image = Image.open(path).convert("RGB")
            image.thumbnail((760, 620))
            self.preview_photo = ImageTk.PhotoImage(image)
            self.preview.configure(image=self.preview_photo)
            messagebox.showinfo(APP_TITLE, f"생성 완료\n{path}")
        except Exception as error:
            messagebox.showerror(APP_TITLE, str(error))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1000, 680)
        self.option_add("*Font", ("Malgun Gothic", 9))

        library_root = app_dir() / "library"
        self.library = PastExamLibrary(library_root)
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        self.generate_tab = GenerateTab(notebook, self.library)
        self.register_tab = RegisterTab(notebook, self.library, self.generate_tab.refresh)
        notebook.add(self.register_tab, text="기출 그림 등록")
        notebook.add(self.generate_tab, text="수치 변경 생성")


if __name__ == "__main__":
    App().mainloop()
