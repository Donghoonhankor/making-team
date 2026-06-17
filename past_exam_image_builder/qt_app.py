import sys
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from library import LibraryEntry, OverlayField, PastExamLibrary, build_source_id
from pdf_tools import PdfDocument, parse_exam_filename


APP_TITLE = "기출이미지생성기"


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def pil_to_pixmap(image):
    rgb = image.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class SelectableImage(QLabel):
    selection_changed = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setMouseTracking(True)
        self.image = None
        self.base_pixmap = None
        self.start = None
        self.selection = None
        self.overlays = []

    def show_image(self, image, overlays=None):
        self.image = image.copy()
        self.base_pixmap = pil_to_pixmap(self.image)
        self.overlays = list(overlays or [])
        self.selection = None
        self.setFixedSize(self.base_pixmap.size())
        self.update()

    def set_selection(self, box):
        self.selection = list(box)
        self.selection_changed.emit(self.selection)
        self.update()

    def mousePressEvent(self, event):
        if self.image and event.button() == Qt.LeftButton:
            self.start = event.position().toPoint()
            self.selection = [self.start.x(), self.start.y(), self.start.x(), self.start.y()]
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            point = event.position().toPoint()
            self.selection = self._normalized_box(self.start, point)
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is not None:
            point = event.position().toPoint()
            self.selection = self._normalized_box(self.start, point)
            self.start = None
            if self.selection[2] - self.selection[0] >= 4 and self.selection[3] - self.selection[1] >= 4:
                self.selection_changed.emit(self.selection)
            self.update()

    def _normalized_box(self, first, second):
        x0, x1 = sorted((first.x(), second.x()))
        y0, y1 = sorted((first.y(), second.y()))
        return [
            max(0, min(self.image.width, x0)),
            max(0, min(self.image.height, y0)),
            max(0, min(self.image.width, x1)),
            max(0, min(self.image.height, y1)),
        ]

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.base_pixmap:
            return
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.base_pixmap)
        painter.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
        for overlay in self.overlays:
            box = overlay.box
            painter.setPen(QPen(QColor("#d12f2f"), 2))
            painter.drawRect(QRect(box[0], box[1], box[2] - box[0], box[3] - box[1]))
            painter.drawText(box[0] + 4, box[1] + 15, overlay.name)
        if self.selection:
            box = self.selection
            painter.setPen(QPen(QColor("#1976d2"), 2))
            painter.drawRect(QRect(box[0], box[1], box[2] - box[0], box[3] - box[1]))
        painter.end()


class RegisterWidget(QWidget):
    def __init__(self, library, refresh_callback):
        super().__init__()
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
        outer = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        for text, callback in [
            ("PDF 열기", self.open_pdf),
            ("◀ 이전", lambda: self.move_page(-1)),
            ("다음 ▶", lambda: self.move_page(1)),
            ("자동 그림 후보", self.auto_candidate),
            ("선택 영역 미리보기", self.preview_crop),
            ("페이지로 돌아가기", self.show_page),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        self.page_label = QLabel("PDF를 선택하세요")
        toolbar.insertWidget(3, self.page_label)
        toolbar.addStretch()
        outer.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        scroll = QScrollArea()
        self.image_label = SelectableImage()
        self.image_label.selection_changed.connect(self.selection_changed)
        scroll.setWidget(self.image_label)
        scroll.setWidgetResizable(False)
        splitter.addWidget(scroll)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        form = QFormLayout()
        defaults = [
            ("학교명", "대방중학교"),
            ("연도", "2024"),
            ("학년", "3"),
            ("학기", "1"),
            ("시험구분", "기말고사"),
            ("문제번호", "24"),
        ]
        self.fields = {}
        for label, default in defaults:
            edit = QLineEdit(default)
            form.addRow(label, edit)
            self.fields[label] = edit
        side_layout.addLayout(form)
        guide = QLabel(
            "페이지에서 그림 전체를 드래그한 뒤 미리보기를 만드세요.\n"
            "그림 미리보기에서는 바꿀 숫자 영역을 드래그해 변수로 추가합니다."
        )
        guide.setWordWrap(True)
        side_layout.addWidget(guide)
        add_button = QPushButton("선택 영역을 숫자 변수로 추가")
        add_button.clicked.connect(self.add_overlay)
        side_layout.addWidget(add_button)
        remove_button = QPushButton("마지막 숫자 영역 삭제")
        remove_button.clicked.connect(self.remove_overlay)
        side_layout.addWidget(remove_button)
        self.overlay_list = QListWidget()
        side_layout.addWidget(self.overlay_list, 1)
        save_button = QPushButton("문제 이미지 등록")
        save_button.clicked.connect(self.save_entry)
        save_button.setMinimumHeight(38)
        side_layout.addWidget(save_button)
        splitter.addWidget(side)
        splitter.setSizes([900, 300])
        outer.addWidget(splitter, 1)

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "기출 PDF 선택", "", "PDF (*.pdf)")
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
        self.image_label.show_image(self.page_image)
        self.page_label.setText(f"{self.page_index + 1} / {self.pdf.page_count} 페이지")

    def move_page(self, delta):
        if not self.pdf:
            return
        self.page_index = max(0, min(self.pdf.page_count - 1, self.page_index + delta))
        self.crop_box = None
        self.overlays = []
        self.show_page()

    def selection_changed(self, box):
        if self.mode == "page":
            self.crop_box = list(box)

    def preview_crop(self):
        if not self.page_image or not self.crop_box:
            QMessageBox.information(self, APP_TITLE, "페이지에서 그림 영역을 먼저 드래그하세요.")
            return
        self.crop_image = self.page_image.crop(tuple(self.crop_box))
        self.mode = "crop"
        self.image_label.show_image(self.crop_image, self.overlays)

    def auto_candidate(self):
        if not self.pdf:
            return
        self.candidates = self.pdf.image_candidates(self.page_index)
        if not self.candidates:
            QMessageBox.information(
                self, APP_TITLE, "독립 이미지 후보를 찾지 못했습니다. 직접 드래그해 주세요."
            )
            return
        self.candidate_index = (self.candidate_index + 1) % len(self.candidates)
        self.crop_box = self.candidates[self.candidate_index]
        self.image_label.set_selection(self.crop_box)
        self.page_label.setText(
            f"{self.page_index + 1} / {self.pdf.page_count} 페이지 · "
            f"후보 {self.candidate_index + 1}/{len(self.candidates)}"
        )

    def add_overlay(self):
        if self.mode != "crop" or not self.image_label.selection:
            QMessageBox.information(self, APP_TITLE, "그림 미리보기에서 숫자 영역을 드래그하세요.")
            return
        name, ok = QInputDialog.getText(self, APP_TITLE, "변수 이름 (예: right_root, angle)")
        if not ok or not name.strip():
            return
        original, ok = QInputDialog.getText(self, APP_TITLE, "현재 표시된 값")
        if not ok:
            return
        font_size, ok = QInputDialog.getInt(self, APP_TITLE, "출력 글자 크기", 28, 8, 100)
        if not ok:
            return
        self.overlays.append(
            OverlayField(name.strip(), original.strip(), list(self.image_label.selection), font_size)
        )
        self.image_label.show_image(self.crop_image, self.overlays)
        self.refresh_overlay_list()

    def remove_overlay(self):
        if self.overlays:
            self.overlays.pop()
            self.image_label.show_image(self.crop_image, self.overlays)
            self.refresh_overlay_list()

    def refresh_overlay_list(self):
        self.overlay_list.clear()
        for overlay in self.overlays:
            self.overlay_list.addItem(f"{overlay.name} = {overlay.original}  {overlay.box}")

    def save_entry(self):
        if not self.crop_image or not self.crop_box or not self.pdf:
            QMessageBox.information(self, APP_TITLE, "그림 영역을 선택하고 미리보기를 만드세요.")
            return
        try:
            source_id = build_source_id(
                self.fields["학교명"].text(),
                self.fields["연도"].text(),
                self.fields["학년"].text(),
                self.fields["학기"].text(),
                self.fields["시험구분"].text(),
                self.fields["문제번호"].text(),
            )
            entry = LibraryEntry(
                source_id=source_id,
                school=self.fields["학교명"].text().strip(),
                year=int(self.fields["연도"].text()),
                grade=int(self.fields["학년"].text()),
                semester=int(self.fields["학기"].text()),
                exam_type=self.fields["시험구분"].text().strip(),
                problem_number=int(self.fields["문제번호"].text()),
                page_number=self.page_index + 1,
                crop_box_pdf=self.pdf.pixel_box_to_pdf(self.crop_box),
                overlays=self.overlays,
            )
            target = self.library.save_entry(entry, self.crop_image, self.pdf_path)
        except Exception as error:
            QMessageBox.critical(self, APP_TITLE, str(error))
            return
        self.refresh_callback()
        QMessageBox.information(self, APP_TITLE, f"등록 완료\n{source_id}\n{target}")


class GenerateWidget(QWidget):
    def __init__(self, library):
        super().__init__()
        self.library = library
        self.current_entry = None
        self.value_edits = {}
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("등록 문제"))
        self.source_combo = QComboBox()
        self.source_combo.currentTextChanged.connect(self.load_selected)
        top.addWidget(self.source_combo, 1)
        refresh_button = QPushButton("목록 새로고침")
        refresh_button.clicked.connect(self.refresh)
        top.addWidget(refresh_button)
        outer.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        scroll = QScrollArea()
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        scroll.setWidget(self.preview)
        scroll.setWidgetResizable(True)
        splitter.addWidget(scroll)
        side = QWidget()
        self.side_layout = QVBoxLayout(side)
        self.info = QLabel("등록 문제를 선택하세요.")
        self.info.setWordWrap(True)
        self.side_layout.addWidget(self.info)
        self.form_widget = QWidget()
        self.form = QFormLayout(self.form_widget)
        self.side_layout.addWidget(self.form_widget)
        self.side_layout.addStretch()
        generate_button = QPushButton("새 PNG 생성")
        generate_button.clicked.connect(self.generate)
        generate_button.setMinimumHeight(38)
        self.side_layout.addWidget(generate_button)
        splitter.addWidget(side)
        splitter.setSizes([900, 300])
        outer.addWidget(splitter, 1)
        self.refresh()

    def refresh(self):
        current = self.source_combo.currentText()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems([item["source_id"] for item in self.library.load_index()])
        if current:
            self.source_combo.setCurrentText(current)
        self.source_combo.blockSignals(False)
        self.load_selected(self.source_combo.currentText())

    def clear_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.value_edits = {}

    def load_selected(self, source_id):
        if not source_id:
            return
        self.current_entry = self.library.load_entry(source_id)
        image = Image.open(self.library.original_path(source_id)).convert("RGB")
        self.preview.setPixmap(pil_to_pixmap(image))
        self.preview.resize(image.width, image.height)
        self.info.setText(
            f"{source_id}\n{self.current_entry.school} {self.current_entry.year} "
            f"{self.current_entry.grade}학년 {self.current_entry.semester}학기 "
            f"{self.current_entry.exam_type} {self.current_entry.problem_number}번"
        )
        self.clear_form()
        for overlay in self.current_entry.overlays:
            edit = QLineEdit(overlay.original)
            self.form.addRow(overlay.name, edit)
            self.value_edits[overlay.name] = edit
        if not self.current_entry.overlays:
            self.form.addRow(QLabel("숫자 영역 없음: 원본 복사본을 생성합니다."))

    def generate(self):
        if not self.current_entry:
            return
        output, _ = QFileDialog.getSaveFileName(
            self,
            "새 PNG 저장",
            f"{self.current_entry.source_id}_변형.png",
            "PNG (*.png)",
        )
        if not output:
            return
        values = {name: edit.text() for name, edit in self.value_edits.items()}
        try:
            path = self.library.render(self.current_entry.source_id, values, output)
            image = Image.open(path).convert("RGB")
            self.preview.setPixmap(pil_to_pixmap(image))
            QMessageBox.information(self, APP_TITLE, f"생성 완료\n{path}")
        except Exception as error:
            QMessageBox.critical(self, APP_TITLE, str(error))


class BatchRegisterWidget(QWidget):
    def __init__(self, library, refresh_callback):
        super().__init__()
        self.library = library
        self.refresh_callback = refresh_callback
        self.pdf = None
        self.pdf_path = None
        self.candidates = []
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        open_button = QPushButton("PDF 선택 및 자동 분석")
        open_button.clicked.connect(self.open_and_analyze)
        top.addWidget(open_button)
        self.status = QLabel("PDF를 선택하면 그림이 있는 문항을 자동으로 찾습니다.")
        top.addWidget(self.status, 1)
        outer.addLayout(top)

        metadata = QHBoxLayout()
        defaults = [
            ("학교명", "대방중학교"),
            ("연도", "2024"),
            ("학년", "3"),
            ("학기", "1"),
            ("시험구분", "기말고사"),
        ]
        self.fields = {}
        for label, default in defaults:
            metadata.addWidget(QLabel(label))
            edit = QLineEdit(default)
            edit.setMaximumWidth(130)
            metadata.addWidget(edit)
            self.fields[label] = edit
        metadata.addStretch()
        outer.addLayout(metadata)

        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["등록", "페이지", "문제번호", "크기", "상태"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self.show_selected)
        splitter.addWidget(self.table)

        preview_scroll = QScrollArea()
        self.preview = QLabel("후보를 선택하면 그림이 표시됩니다.")
        self.preview.setAlignment(Qt.AlignCenter)
        preview_scroll.setWidget(self.preview)
        preview_scroll.setWidgetResizable(True)
        splitter.addWidget(preview_scroll)
        splitter.setSizes([520, 700])
        outer.addWidget(splitter, 1)

        actions = QHBoxLayout()
        check_all = QPushButton("전체 선택")
        check_all.clicked.connect(lambda: self.set_all_checked(True))
        actions.addWidget(check_all)
        uncheck_all = QPushButton("전체 해제")
        uncheck_all.clicked.connect(lambda: self.set_all_checked(False))
        actions.addWidget(uncheck_all)
        actions.addStretch()
        register_button = QPushButton("체크한 그림 일괄 등록")
        register_button.clicked.connect(self.register_checked)
        register_button.setMinimumHeight(38)
        actions.addWidget(register_button)
        outer.addLayout(actions)

    def open_and_analyze(self):
        path, _ = QFileDialog.getOpenFileName(self, "기출 PDF 선택", "", "PDF (*.pdf)")
        if not path:
            return
        if self.pdf:
            self.pdf.close()
        self.pdf_path = path
        self.pdf = PdfDocument(path, zoom=2.0)
        metadata = parse_exam_filename(path)
        field_mapping = {
            "school": "학교명",
            "year": "연도",
            "grade": "학년",
            "semester": "학기",
            "exam_type": "시험구분",
        }
        for key, field_name in field_mapping.items():
            if metadata.get(key):
                self.fields[field_name].setText(str(metadata[key]))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.candidates = self.pdf.batch_diagram_candidates()
        finally:
            QApplication.restoreOverrideCursor()
        self._assign_image_indexes()
        self.populate_table()
        self.status.setText(
            f"{Path(path).name} · 시험지 {self.pdf.page_count}페이지 · "
            f"그림 후보 {len(self.candidates)}개"
        )

    def _assign_image_indexes(self):
        counts = {}
        totals = {}
        for candidate in self.candidates:
            number = int(candidate.get("problem_number") or 0)
            totals[number] = totals.get(number, 0) + 1
        for candidate in self.candidates:
            number = int(candidate.get("problem_number") or 0)
            counts[number] = counts.get(number, 0) + 1
            candidate["image_index"] = counts[number]
            candidate["image_total"] = totals[number]

    def populate_table(self):
        self.table.setRowCount(len(self.candidates))
        for row, candidate in enumerate(self.candidates):
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            checked.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, checked)
            page_item = QTableWidgetItem(str(candidate["page_number"]))
            page_item.setFlags(page_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, page_item)
            number_item = QTableWidgetItem(str(candidate.get("problem_number") or ""))
            self.table.setItem(row, 2, number_item)
            size_item = QTableWidgetItem(f'{candidate["width"]}×{candidate["height"]}')
            size_item.setFlags(size_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 3, size_item)
            status_item = QTableWidgetItem("자동 검출")
            status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 4, status_item)
        if self.candidates:
            self.table.selectRow(0)

    def show_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.pdf:
            return
        row = rows[0].row()
        image = self.pdf.crop_candidate(self.candidates[row])
        image.thumbnail((760, 650))
        self.preview.setPixmap(pil_to_pixmap(image))

    def set_all_checked(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def register_checked(self):
        if not self.pdf or not self.candidates:
            QMessageBox.information(self, APP_TITLE, "먼저 PDF를 자동 분석하세요.")
            return
        selected = []
        for row, candidate in enumerate(self.candidates):
            if self.table.item(row, 0).checkState() != Qt.Checked:
                continue
            try:
                problem_number = int(self.table.item(row, 2).text())
            except ValueError:
                QMessageBox.warning(self, APP_TITLE, f"{row + 1}행 문제번호를 확인하세요.")
                return
            selected.append((row, candidate, problem_number))
        if not selected:
            QMessageBox.information(self, APP_TITLE, "등록할 그림을 체크하세요.")
            return

        number_totals = {}
        for _row, _candidate, number in selected:
            number_totals[number] = number_totals.get(number, 0) + 1
        number_indexes = {}
        registered = 0
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for row, candidate, problem_number in selected:
                number_indexes[problem_number] = number_indexes.get(problem_number, 0) + 1
                image_index = number_indexes[problem_number]
                use_suffix = number_totals[problem_number] > 1
                source_id = build_source_id(
                    self.fields["학교명"].text(),
                    self.fields["연도"].text(),
                    self.fields["학년"].text(),
                    self.fields["학기"].text(),
                    self.fields["시험구분"].text(),
                    problem_number,
                    image_index if use_suffix else None,
                )
                entry = LibraryEntry(
                    source_id=source_id,
                    school=self.fields["학교명"].text().strip(),
                    year=int(self.fields["연도"].text()),
                    grade=int(self.fields["학년"].text()),
                    semester=int(self.fields["학기"].text()),
                    exam_type=self.fields["시험구분"].text().strip(),
                    problem_number=problem_number,
                    page_number=int(candidate["page_number"]),
                    crop_box_pdf=list(candidate["pdf_box"]),
                    overlays=[],
                    image_index=image_index,
                )
                self.library.save_entry(entry, self.pdf.crop_candidate(candidate))
                self.table.item(row, 4).setText("등록 완료")
                registered += 1
        except Exception as error:
            QMessageBox.critical(self, APP_TITLE, str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh_callback()
        QMessageBox.information(
            self,
            APP_TITLE,
            f"{registered}개 그림을 일괄 등록했습니다.\n"
            "숫자를 바꿔야 하는 그림만 개별 등록 화면에서 숫자 영역을 추가하면 됩니다.",
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 820)
        self.setMinimumSize(1000, 680)
        library = PastExamLibrary(app_dir() / "library")
        tabs = QTabWidget()
        generate = GenerateWidget(library)
        register = RegisterWidget(library, generate.refresh)
        batch = BatchRegisterWidget(library, generate.refresh)
        tabs.addTab(batch, "PDF 일괄 분석·등록")
        tabs.addTab(register, "기출 그림 등록")
        tabs.addTab(generate, "수치 변경 생성")
        self.setCentralWidget(tabs)


if __name__ == "__main__":
    application = QApplication(sys.argv)
    application.setFont(QFont("Malgun Gothic", 9))
    window = MainWindow()
    window.show()
    sys.exit(application.exec())
