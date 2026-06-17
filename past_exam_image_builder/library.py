import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


EXAM_CODES = {
    "중간고사": "MID",
    "기말고사": "FINAL",
    "중간": "MID",
    "기말": "FINAL",
}


def safe_name(value):
    text = re.sub(r'[<>:"/\\|?*]+', "_", str(value or "").strip())
    return re.sub(r"\s+", " ", text).strip(" .") or "UNKNOWN"


def build_source_id(
    school, year, grade, semester, exam_type, problem_number, image_index=None
):
    exam_code = EXAM_CODES.get(str(exam_type).strip(), safe_name(exam_type).upper())
    source_id = (
        f"{safe_name(school)}-{int(year)}-G{int(grade)}-S{int(semester)}-"
        f"{exam_code}-Q{int(problem_number):03d}"
    )
    if image_index is not None:
        source_id += f"-IMG{int(image_index):02d}"
    return source_id


@dataclass
class OverlayField:
    name: str
    original: str
    box: list
    font_size: int = 28
    color: str = "#000000"


@dataclass
class LibraryEntry:
    source_id: str
    school: str
    year: int
    grade: int
    semester: int
    exam_type: str
    problem_number: int
    page_number: int
    crop_box_pdf: list
    mode: str = "overlay"
    overlays: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    image_index: int = 1


class PastExamLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def load_index(self):
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def save_index(self, items):
        self.index_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def entry_dir(self, source_id):
        return self.root / safe_name(source_id)

    def save_entry(self, entry, image, source_pdf=None):
        now = datetime.now().isoformat(timespec="seconds")
        entry.created_at = entry.created_at or now
        entry.updated_at = now
        target = self.entry_dir(entry.source_id)
        target.mkdir(parents=True, exist_ok=True)

        original_path = target / "original.png"
        image.convert("RGB").save(original_path, quality=95)
        image.copy().thumbnail((600, 600))
        preview = image.copy()
        preview.thumbnail((600, 600))
        preview.convert("RGB").save(target / "preview.png", quality=92)

        recipe = asdict(entry)
        (target / "recipe.json").write_text(
            json.dumps(recipe, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if source_pdf:
            source_path = Path(source_pdf)
            if source_path.exists():
                shutil.copy2(source_path, target / "source.pdf")

        index = [item for item in self.load_index() if item.get("source_id") != entry.source_id]
        index.append(
            {
                "source_id": entry.source_id,
                "school": entry.school,
                "year": entry.year,
                "grade": entry.grade,
                "semester": entry.semester,
                "exam_type": entry.exam_type,
                "problem_number": entry.problem_number,
                "preview": str(target / "preview.png"),
                "updated_at": entry.updated_at,
            }
        )
        index.sort(
            key=lambda item: (
                item.get("school", ""),
                item.get("year", 0),
                item.get("grade", 0),
                item.get("semester", 0),
                item.get("problem_number", 0),
            )
        )
        self.save_index(index)
        return target

    def load_entry(self, source_id):
        recipe_path = self.entry_dir(source_id) / "recipe.json"
        data = json.loads(recipe_path.read_text(encoding="utf-8"))
        data["overlays"] = [OverlayField(**item) for item in data.get("overlays", [])]
        return LibraryEntry(**data)

    def original_path(self, source_id):
        return self.entry_dir(source_id) / "original.png"

    def render(self, source_id, values, output_path):
        entry = self.load_entry(source_id)
        image = Image.open(self.original_path(source_id)).convert("RGB")
        draw = ImageDraw.Draw(image)
        for overlay in entry.overlays:
            box = tuple(int(value) for value in overlay.box)
            draw.rectangle(box, fill="white")
            value = str(values.get(overlay.name, overlay.original))
            font = load_font(overlay.font_size)
            text_box = draw.textbbox((0, 0), value, font=font)
            text_width = text_box[2] - text_box[0]
            text_height = text_box[3] - text_box[1]
            x = box[0] + max(0, (box[2] - box[0] - text_width) / 2)
            y = box[1] + max(0, (box[3] - box[1] - text_height) / 2) - text_box[1]
            draw.text((x, y), value, fill=overlay.color, font=font)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output)
        return output


def load_font(size):
    candidates = [
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), max(8, int(size)))
    return ImageFont.load_default()
