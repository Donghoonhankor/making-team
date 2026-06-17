from pathlib import Path
import re

import fitz
from PIL import Image


def parse_exam_filename(path):
    name = Path(path).stem
    result = {}
    year_match = re.search(r"(20\d{2})", name)
    grade_semester_match = re.search(r"(?<!\d)([1-3])\s*[-_.]\s*([1-2])(?!\d)", name)
    school_match = re.search(r"\(([^)]+)\)", name)
    if year_match:
        result["year"] = year_match.group(1)
    if grade_semester_match:
        result["grade"] = grade_semester_match.group(1)
        result["semester"] = grade_semester_match.group(2)
    if "기말" in name:
        result["exam_type"] = "기말고사"
    elif "중간" in name:
        result["exam_type"] = "중간고사"
    if school_match:
        school = re.sub(r"\s+", "", school_match.group(1))
        school = re.sub(r"(김|이|박|최|정|강|조|윤|장|임)[가-힣]{1,3}$", "", school)
        result["school"] = school
    return result


class PdfDocument:
    def __init__(self, path, zoom=2.0):
        self.path = Path(path)
        self.document = fitz.open(str(self.path))
        self.zoom = float(zoom)

    @property
    def page_count(self):
        return self.document.page_count

    def render_page(self, page_index):
        page = self.document.load_page(page_index)
        matrix = fitz.Matrix(self.zoom, self.zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        return image

    def image_candidates(self, page_index):
        page = self.document.load_page(page_index)
        page_area = page.rect.width * page.rect.height
        candidates = []
        for info in page.get_image_info(xrefs=True):
            bbox = fitz.Rect(info.get("bbox"))
            area = bbox.width * bbox.height
            if bbox.width < 40 or bbox.height < 40:
                continue
            if page_area and area / page_area > 0.85:
                continue
            candidates.append(
                [
                    int(bbox.x0 * self.zoom),
                    int(bbox.y0 * self.zoom),
                    int(bbox.x1 * self.zoom),
                    int(bbox.y1 * self.zoom),
                ]
            )
        return candidates

    def batch_diagram_candidates(self, first_page=0, last_page=None):
        last_page = self.page_count if last_page is None else min(last_page, self.page_count)
        results = []
        seen = set()
        previous_problem_number = 0
        for page_index in range(max(0, first_page), last_page):
            page = self.document.load_page(page_index)
            page_text = page.get_text("text")
            if re.search(r"답\s*지|해설지", page_text):
                break

            markers = self._question_markers(page)
            for info in page.get_image_info(xrefs=True):
                bbox = fitz.Rect(info.get("bbox"))
                if bbox.width < 95 or bbox.height < 65:
                    continue
                if bbox.width > page.rect.width * 0.88 and bbox.height > page.rect.height * 0.75:
                    continue
                key = (
                    page_index,
                    round(bbox.x0, 1),
                    round(bbox.y0, 1),
                    round(bbox.x1, 1),
                    round(bbox.y1, 1),
                )
                if key in seen:
                    continue
                seen.add(key)

                problem_number = self._suggest_problem_number(
                    page, bbox, markers, previous_problem_number
                )
                padded = fitz.Rect(
                    max(0, bbox.x0 - 5),
                    max(0, bbox.y0 - 5),
                    min(page.rect.width, bbox.x1 + 5),
                    min(page.rect.height, bbox.y1 + 5),
                )
                pixel_box = [
                    int(padded.x0 * self.zoom),
                    int(padded.y0 * self.zoom),
                    int(padded.x1 * self.zoom),
                    int(padded.y1 * self.zoom),
                ]
                results.append(
                    {
                        "page_index": page_index,
                        "page_number": page_index + 1,
                        "problem_number": problem_number,
                        "pdf_box": [
                            round(padded.x0, 2),
                            round(padded.y0, 2),
                            round(padded.x1, 2),
                            round(padded.y1, 2),
                        ],
                        "pixel_box": pixel_box,
                        "width": int(pixel_box[2] - pixel_box[0]),
                        "height": int(pixel_box[3] - pixel_box[1]),
                    }
                )
            anchor_numbers = [
                marker["number"] for marker in markers if marker.get("is_anchor")
            ]
            if anchor_numbers:
                previous_problem_number = max(previous_problem_number, max(anchor_numbers))
        return results

    def crop_candidate(self, candidate):
        page_image = self.render_page(int(candidate["page_index"]))
        return page_image.crop(tuple(candidate["pixel_box"]))

    def _question_markers(self, page):
        markers = []
        for block in page.get_text("blocks"):
            text = str(block[4] or "").strip().replace("\n", " ")
            for match in re.finditer(r"(?<!\d)(\d{1,2})\s*[\.\)]", text):
                number = int(match.group(1))
                if 1 <= number <= 60:
                    markers.append(
                        {
                            "number": number,
                            "box": fitz.Rect(block[:4]),
                            "text": text,
                            "is_anchor": bool(re.match(rf"^\s*{number}\s*\.", text)),
                        }
                    )
        return markers

    def _suggest_problem_number(
        self, page, image_box, markers, previous_problem_number=0
    ):
        center = fitz.Point(
            (image_box.x0 + image_box.x1) / 2,
            (image_box.y0 + image_box.y1) / 2,
        )
        same_column_markers = []
        for marker in markers:
            box = marker["box"]
            marker_center_x = (box.x0 + box.x1) / 2
            if (
                (center.x < page.rect.width / 2 and marker_center_x < page.rect.width / 2)
                or (center.x >= page.rect.width / 2 and marker_center_x >= page.rect.width / 2)
            ):
                same_column_markers.append(marker)

        nearby_continuation = [
            marker
            for marker in same_column_markers
            if not marker["is_anchor"]
            and marker["number"] >= previous_problem_number
            and marker["box"].y0 <= image_box.y1 + 80
            and marker["box"].y1 >= image_box.y0 - 80
        ]
        if nearby_continuation:
            return min(
                nearby_continuation,
                key=lambda marker: abs(
                    ((marker["box"].y0 + marker["box"].y1) / 2) - center.y
                ),
            )["number"]

        first_anchor_y = min(
            (
                marker["box"].y0
                for marker in same_column_markers
                if marker["is_anchor"]
            ),
            default=None,
        )
        if (
            previous_problem_number
            and first_anchor_y is not None
            and image_box.y1 < first_anchor_y
        ):
            return previous_problem_number

        scored = []
        for marker in markers:
            box = marker["box"]
            marker_center = fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
            dx = abs(center.x - marker_center.x)
            dy = abs(center.y - marker_center.y)
            same_column = (
                (center.x < page.rect.width / 2 and marker_center.x < page.rect.width / 2)
                or (center.x >= page.rect.width / 2 and marker_center.x >= page.rect.width / 2)
            )
            score = dy + dx * (0.18 if same_column else 0.65)
            if marker["is_anchor"] and box.y0 <= image_box.y1:
                score *= 0.82
            scored.append((score, marker["number"]))
        return min(scored)[1] if scored else 0

    def pixel_box_to_pdf(self, box):
        return [round(float(value) / self.zoom, 2) for value in box]

    def close(self):
        self.document.close()
