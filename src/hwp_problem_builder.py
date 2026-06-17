import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


REQUIRED_TEMPLATE_TOKENS = (
    "{{QUESTIONS}}",
)


@dataclass
class Token:
    kind: str
    value: str
    number: int = 0


@dataclass
class DocumentContent:
    student_name: str
    date_filename: str
    questions: str
    answers: str


class TemplateError(RuntimeError):
    pass


def select_file(title, file_filter, initial_directory=None):
    if not sys.platform.startswith("win"):
        return input(f"{title}: ").strip().strip('"')

    initial_directory = str(initial_directory or Path.home()).replace("'", "''")
    script = "\n".join(
        [
            "Add-Type -AssemblyName System.Windows.Forms",
            "$dialog = New-Object System.Windows.Forms.OpenFileDialog",
            f"$dialog.Title = '{title.replace(chr(39), chr(39) * 2)}'",
            f"$dialog.Filter = '{file_filter.replace(chr(39), chr(39) * 2)}'",
            f"$dialog.InitialDirectory = '{initial_directory}'",
            "$dialog.Multiselect = $false",
            "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {",
            "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "  Write-Output $dialog.FileName",
            "}",
        ]
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    selected = completed.stdout.strip()
    return selected.splitlines()[-1].strip() if selected else ""


def select_input_file():
    return select_file(
        "HWP로 변환할 문제 텍스트 파일을 선택하세요",
        "텍스트 파일 (*.txt)|*.txt|모든 파일 (*.*)|*.*",
    )


def select_template_file(initial_directory=None):
    return select_file(
        "학원 HWP 템플릿 파일을 선택하세요",
        "한글 문서 (*.hwp)|*.hwp|모든 파일 (*.*)|*.*",
        initial_directory,
    )


def show_message(title, message, is_error=False):
    print(message)
    if not sys.platform.startswith("win"):
        return
    icon = "Error" if is_error else "Information"
    script = "\n".join(
        [
            "Add-Type -AssemblyName System.Windows.Forms",
            f"$title = '{str(title).replace(chr(39), chr(39) * 2)}'",
            f"$message = '{str(message).replace(chr(39), chr(39) * 2)}'",
            "[System.Windows.Forms.MessageBox]::Show(",
            "  $message, $title,",
            "  [System.Windows.Forms.MessageBoxButtons]::OK,",
            f"  [System.Windows.Forms.MessageBoxIcon]::{icon}",
            ") | Out-Null",
        ]
    )
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            timeout=120,
        )
    except Exception:
        pass


def parse_tokens(text):
    return parse_tokens_from_number(text, 1)[0]


def strip_bare_image_prompt_blocks(text):
    return re.sub(
        r"(?im)(?<!\[)IMAGE_PROMPT\s*(?:\(\d+\)|\d+)?\s*:\s*\n?"
        r"(?:[ \t]*[A-Za-z_][A-Za-z0-9_]*\s*=.*(?:\n|$))+",
        "",
        text,
    )


INLINE_MATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])("
    r"[A-Z]\s*\(\s*[^(),\n]+\s*,\s*[^(),\n]+\s*\)"
    r"|△\s*[A-Z]{3}"
    r"|\(\s*[^(),\n]+\s*,\s*[^(),\n]+\s*\)"
    r"|[A-Za-z]\s*\(\s*[A-Za-z]\s*(?:<=|>=|!=|[<>=≠])\s*[-+]?\d+(?:\.\d+)?\s*\)"
    r"|[xy](?=(?:축|좌표|절편|성분|값))"
    r"|[xy](?=(?:km|cm|mm|m|L)(?![A-Za-z]))"
    r"|(?!(?:cm|mm|km|ml|kg)(?=[^A-Za-z]|$))[a-z]{2,4}(?=(?:의|은|는|이|가|을|를|와|과|에|에서|값|좌표|성분))"
    r"|[A-Z]{2,8}"
    r"|[A-KM-Z]"
    r"|[a-z](?![A-Za-z0-9])"
    r")"
)


AUTO_MATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9가-힣])("
    r"(?:[-+]?\d+\s*/\s*[-+]?\d+)"
    r"|"
    r"(?:"
    r"(?=[A-Za-z0-9(.\-+])"
    r"[A-Za-z0-9²³⁴√^+\-*/=×÷<>≤≥≠±().,\s]+"
    r")"
    r")"
)


def is_auto_formula_candidate(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    if re.fullmatch(r"[-+]?\d+\s*/\s*[-+]?\d+", text):
        return True
    if not re.search(r"[A-Za-z]", text):
        return False
    if re.search(r"[=<>≤≥≠]", text):
        return True
    if re.search(r"[²³⁴√^]", text):
        return True
    if re.search(r"\d\s*[A-Za-z]|[A-Za-z]\s*\d", text):
        return True
    if re.search(r"[+\-*/×÷]", text) and len(text) > 1:
        return True
    return False


def trim_auto_formula(value):
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[,;:]+", "", text).strip()
    text = re.sub(r"[,;:]+$", "", text).strip()
    return text


def append_text_with_inline_math(tokens, text):
    cursor = 0
    while cursor < len(text):
        auto_match = AUTO_MATH_PATTERN.search(text, cursor)
        inline_match = INLINE_MATH_PATTERN.search(text, cursor)
        matches = [m for m in (auto_match, inline_match) if m]
        if not matches:
            tokens.append(Token("text", text[cursor:]))
            break

        match = min(matches, key=lambda item: item.start())
        raw_value = match.group(1)
        formula = trim_auto_formula(raw_value)
        is_auto = match.re is AUTO_MATH_PATTERN
        if is_auto and not is_auto_formula_candidate(formula):
            next_cursor = match.start() + max(1, len(raw_value))
            tokens.append(Token("text", text[cursor:next_cursor]))
            cursor = next_cursor
            continue

        leading = raw_value[: len(raw_value) - len(raw_value.lstrip())]
        trailing = raw_value[len(raw_value.rstrip()) :]
        formula_start = match.start() + len(leading)
        formula_end = match.end() - len(trailing)
        if match.start() > cursor:
            tokens.append(Token("text", text[cursor : formula_start]))
        elif leading:
            tokens.append(Token("text", leading))
        tokens.append(Token("formula", formula))
        cursor = formula_end


def parse_tokens_from_number(text, fallback_image_number):
    text = strip_bare_image_prompt_blocks(text)
    pattern = re.compile(
        r"("
        r"<보기>\s*([\s\S]*?)\s*</보기>"
        r"|"
        r"\[이미지\s*필요\s*(?:\((\d+)\)|(\d+))?\s*:[\s\S]*?\]"
        r"|\[IMAGE_PROMPT\s*(?:\((\d+)\)|(\d+))?\s*:[\s\S]*?\]"
        r"|\[수식\s*:\s*([\s\S]*?)\]"
        r")",
        re.IGNORECASE,
    )
    tokens = []
    cursor = 0

    for match in pattern.finditer(text):
        if match.start() > cursor:
            append_text_with_inline_math(
                tokens, text[cursor : match.start()]
            )

        full = match.group(1)
        choice_box = match.group(2)
        korean_image_number = match.group(3) or match.group(4)
        formula = match.group(7)

        if choice_box is not None:
            tokens.append(Token("choice_box", choice_box.strip()))
        elif full.upper().startswith("[IMAGE_PROMPT"):
            pass
        elif full.startswith("[이미지"):
            number = (
                int(korean_image_number)
                if korean_image_number
                else fallback_image_number
            )
            fallback_image_number = max(fallback_image_number, number + 1)
            tokens.append(Token("image", "", number))
        else:
            tokens.append(Token("formula", formula or ""))

        cursor = match.end()

    if cursor < len(text):
        append_text_with_inline_math(tokens, text[cursor:])
    return tokens, fallback_image_number


def normalize_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    return text.replace("`", "")


def split_text_chunks(text, limit=180):
    chunks = []
    current = ""
    for piece in re.split(r"(\s+)", text):
        if len(current) + len(piece) > limit and current:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return chunks


def convert_formula_to_hwp(value):
    text = str(value or "").strip()
    if not text:
        return ""

    unicode_replacements = {
        "\u00b2": "^{2}",
        "\u00b3": "^{3}",
        "\u2074": "^{4}",
        "\u00d7": "times",
        "\u00f7": "div",
        "\u00b1": " plusminus ",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u2260": "!=",
        "\u2212": "-",
    }
    for source, target in unicode_replacements.items():
        text = text.replace(source, target)
    text = re.sub(
        r"(?<![A-Za-z])(\d*)\s*\u221a\s*\(([^()]+)\)",
        lambda match: (
            (match.group(1) + " " if match.group(1) else "")
            + "sqrt {"
            + match.group(2).strip()
            + "}"
        ),
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z])(\d*)\s*\u221a\s*([A-Za-z0-9]+)",
        lambda match: (
            (match.group(1) + " " if match.group(1) else "")
            + "sqrt {"
            + match.group(2)
            + "}"
        ),
        text,
    )

    replacements = {
        "²": "^{2}",
        "³": "^{3}",
        "⁴": "^{4}",
        "×": "times",
        "÷": "div",
        "±": "plusminus",
        "≤": "<=",
        "≥": ">=",
        "≠": "!=",
        "π": "pi",
        "θ": "theta",
        "∠": "angle",
        "△": "triangle ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\^(?!\{)(-?\d+)", r"^{\1}", text)
    text = re.sub(
        r"\\+frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
        r"{\1} over {\2}",
        text,
    )
    text = re.sub(
        r"\bfrac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
        r"{\1} over {\2}",
        text,
    )
    text = re.sub(r"√\s*\(([^()]+)\)", r"sqrt {\1}", text)
    text = re.sub(r"√\s*([A-Za-z0-9가-힣]+)", r"sqrt {\1}", text)

    text = re.sub(r"\\+Rightarrow\b", " ⇒ ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+rightarrow\b", " → ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+Leftarrow\b", " ⇐ ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+leftrightarrow\b", " ↔ ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+neq\b", " ≠ ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+ne\b", " ≠ ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+vec\s*\{([^{}]+)\}", r"vec {\1}", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+overline\s*\{([^{}]+)\}", r"bar {\1}", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+angle\b", "angle ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\+triangle\b", "triangle ", text, flags=re.IGNORECASE)

    def fraction_repl(match):
        return "{" + match.group(1) + "} over {" + match.group(2) + "}"

    text = re.sub(
        r"\(([^()\n]+)\)\s*/\s*([A-Za-z0-9]+)",
        lambda match: "{"
        + match.group(1).strip()
        + "} over {"
        + match.group(2).strip()
        + "}",
        text,
    )
    return re.sub(
        r"(?<![\w}])([A-Za-z0-9]+)\s*/\s*([A-Za-z0-9]+)(?![\w{])",
        fraction_repl,
        text,
    )


def get_image_path(input_path, number):
    for suffix in (".PNG", ".png", ".JPG", ".jpg", ".JPEG", ".jpeg"):
        candidate = input_path.with_name(
            f"{input_path.stem}_이미지{number}{suffix}"
        )
        if candidate.exists():
            return candidate
    return None


def sanitize_filename_part(value, fallback):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned or fallback


def normalize_date(date_value=None):
    if date_value:
        digits = re.sub(r"\D", "", str(date_value))
        if len(digits) == 8:
            parsed = datetime.strptime(digits, "%Y%m%d")
        else:
            raise ValueError(
                "날짜는 YYYYMMDD 형식으로 입력해야 합니다. 예: 20260615"
            )
    else:
        parsed = datetime.now()
    return parsed.strftime("%Y. %m. %d."), parsed.strftime("%Y%m%d")


def parse_document_content(
    text,
    input_path,
    student_override="",
    date_override="",
):
    normalized = normalize_text(text).lstrip("\ufeff")
    answer_match = re.search(
        r"(?im)^\s*\[(?:정답(?:\s*및\s*해설)?|답안)\]\s*$",
        normalized,
    )
    if answer_match:
        question_block = normalized[: answer_match.start()].rstrip()
        answers = normalized[answer_match.end() :].strip()
        question_start = re.search(r"(?im)^\s*문항\s*1\s*\.", question_block)
        questions = (
            question_block[question_start.start() :].strip()
            if question_start
            else question_block.strip()
        )
    else:
        inline = split_inline_answer_document(normalized)
        if not inline:
            raise ValueError(
                "입력 텍스트에서 '[정답 및 해설]' 또는 '[정답]' 구역을 찾지 못했고, "
                "문항별 '정답:'/'해설:' 인라인 형식도 찾지 못했습니다."
            )
        questions, answers = inline
    if not questions:
        raise ValueError("입력 텍스트에서 문제 본문을 찾지 못했습니다.")
    if not answers:
        raise ValueError("입력 텍스트의 답안 구역이 비어 있습니다.")

    first_line = next(
        (line.strip() for line in normalized.splitlines() if line.strip()),
        input_path.stem,
    )
    parsed_student = ""
    if " - " in first_line:
        parsed_student, _ = [
            part.strip() for part in first_line.split(" - ", 1)
        ]

    if not parsed_student:
        parsed_student = re.split(r"[_-]", input_path.stem, maxsplit=1)[0].strip()

    _, date_filename = normalize_date(date_override)
    return DocumentContent(
        student_name=(student_override or parsed_student or "학생").strip(),
        date_filename=date_filename,
        questions=questions,
        answers=answers,
    )


def split_inline_answer_document(text):
    question_start = re.search(r"(?im)^\s*문항\s*1\s*\.", text)
    source = text[question_start.start() :].strip() if question_start else text.strip()
    matches = list(re.finditer(r"(?im)^\s*문항\s*(\d+)\s*\.", source))
    if not matches:
        return None

    question_parts = []
    answer_parts = []
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        body = source[match.end() : end].strip()
        answer_marker = re.search(r"(?im)^\s*정답\s*:", body)
        if not answer_marker:
            return None

        question_body = body[: answer_marker.start()].rstrip()
        answer_body = body[answer_marker.start() :].strip()
        if not question_body or not answer_body:
            return None

        question_parts.append(f"문항{number}.\n{question_body}")
        answer_parts.append(f"문항{number}.\n{answer_body}")

    return "\n\n".join(question_parts), "\n\n".join(answer_parts)


def split_numbered_sections(text, section_name):
    matches = list(re.finditer(r"(?im)^\s*문항\s*(\d+)\s*\.", text))
    if not matches:
        raise ValueError(f"{section_name}에서 '문항1.' 형식의 문항을 찾지 못했습니다.")

    sections = []
    seen = set()
    for index, match in enumerate(matches):
        number = int(match.group(1))
        if number in seen:
            raise ValueError(f"{section_name}에 문항{number}이 두 번 이상 있습니다.")
        seen.add(number)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            raise ValueError(f"{section_name}의 문항{number} 내용이 비어 있습니다.")
        sections.append((number, body))
    return sections


def unique_output_path(path):
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("출력 파일 이름을 만들 수 없습니다. 기존 파일을 정리해 주세요.")


def default_output_path(input_path, content):
    student = sanitize_filename_part(content.student_name, "학생")
    filename = f"{student}_{content.date_filename}_문제지.hwp"
    return unique_output_path(input_path.with_name(filename))


def hide_hwp_window(hwp):
    for action in (
        lambda: setattr(hwp, "Visible", False),
        lambda: setattr(hwp.XHwpWindows.Item(0), "Visible", False),
    ):
        try:
            action()
        except Exception:
            pass


def register_file_path_checker(hwp):
    attempts = (
        ("FilePathCheckDLL", "FilePathCheckerModule"),
        ("FilePathCheckDLL", "FilePathCheckDLL"),
    )
    errors = []
    for dll_name, module_name in attempts:
        try:
            result = hwp.RegisterModule(dll_name, module_name)
            if result is not False:
                print(
                    f"HWP file path checker registered: {dll_name}/{module_name} ({result})",
                    flush=True,
                )
                return
            errors.append(f"{dll_name}/{module_name}: returned False")
        except Exception as exc:
            errors.append(f"{dll_name}/{module_name}: {exc}")
    print(
        "WARNING: HWP file path checker registration failed. "
        "Security dialogs will be handled by the auto-allow watcher. "
        + " / ".join(errors),
        flush=True,
    )


def click_hwp_security_dialog_once():
    try:
        import win32con
        import win32gui
    except Exception:
        return False

    clicked = False

    def enum_windows(hwnd, _):
        nonlocal clicked
        if clicked or not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""

        child_texts = []
        buttons = []

        def enum_children(child, __):
            text = win32gui.GetWindowText(child) or ""
            if text:
                child_texts.append(text)
            try:
                class_name = win32gui.GetClassName(child)
            except Exception:
                class_name = ""
            if class_name == "Button":
                buttons.append((child, text))

        try:
            win32gui.EnumChildWindows(hwnd, enum_children, None)
        except Exception:
            return

        joined = "\n".join(child_texts)
        has_allow_button = any("모두 허용" in text for _, text in buttons) or any(
            text.strip().startswith("접근 허용") for _, text in buttons
        )
        if (
            "외부에서 접근" not in joined
            and "접근을 허용" not in joined
            and not (title == "한글" and has_allow_button)
        ):
            return

        target = None
        for child, text in buttons:
            if "모두 허용" in text:
                target = child
                break
        if target is None:
            for child, text in buttons:
                if text.strip().startswith("접근 허용"):
                    target = child
                    break
        if target is None:
            return

        try:
            win32gui.SendMessage(target, win32con.BM_CLICK, 0, 0)
            clicked = True
            print("HWP security dialog auto-allowed.", flush=True)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(enum_windows, None)
    except Exception:
        return False
    return clicked


def start_hwp_security_dialog_watcher():
    if not sys.platform.startswith("win"):
        return lambda: None

    script = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class HwpNativeWindow {
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
$deadline = (Get-Date).AddSeconds(600)
$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Button
)
while ((Get-Date) -lt $deadline) {
  try {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $windows = $root.FindAll(
      [System.Windows.Automation.TreeScope]::Children,
      [System.Windows.Automation.Condition]::TrueCondition
    )
    foreach ($window in $windows) {
      try {
        $process = Get-Process -Id $window.Current.ProcessId -ErrorAction Stop
      } catch {
        continue
      }
      if ($process.ProcessName -ne 'Hwp') {
        continue
      }
      $buttons = $window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        $buttonCondition
      )
      $dialogButtons = @()
      foreach ($button in $buttons) {
        if ($button.Current.ClassName -eq 'DialogButtonImpl') {
          $dialogButtons += $button
        }
      }
      if ($dialogButtons.Count -lt 2) {
        continue
      }
      [HwpNativeWindow]::SetForegroundWindow([IntPtr]$window.Current.NativeWindowHandle) | Out-Null
      Start-Sleep -Milliseconds 120
      [System.Windows.Forms.SendKeys]::SendWait('n')
      Write-Output "HWP security dialog auto-allowed by accelerator"
      Start-Sleep -Milliseconds 800
      break
    }
  } catch {
  }
  Start-Sleep -Milliseconds 150
}
"""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )

    def stop():
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    return stop


def dispatch_hwp():
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "HWP 자동화 모듈을 불러오지 못했습니다. "
            "HWP 생성기를 최신 버전으로 다시 빌드해야 합니다. "
            f"상세 오류: {exc}"
        ) from exc

    pythoncom.CoInitialize()
    try:
        hwp = win32com.client.dynamic.Dispatch("HWPFrame.HwpObject")
    except Exception as exc:
        raise RuntimeError(
            "한글 자동화 서버를 시작하지 못했습니다. 열려 있는 한글 창을 "
            f"모두 종료한 뒤 다시 실행하세요. COM 오류: {exc}"
        ) from exc

    register_file_path_checker(hwp)
    hide_hwp_window(hwp)
    return hwp


def run_action(hwp, action_name):
    try:
        return hwp.HAction.Run(action_name)
    except Exception:
        return False


def wait_for_hwp_com(attempt):
    try:
        import pythoncom

        pythoncom.PumpWaitingMessages()
    except Exception:
        pass
    time.sleep(0.15 * (2 ** attempt))


def insert_text_chunk(hwp, chunk):
    last_error = None
    for attempt in range(4):
        try:
            pset = hwp.HParameterSet.HInsertText
            hwp.HAction.GetDefault("InsertText", pset.HSet)
            pset.Text = chunk
            result = hwp.HAction.Execute("InsertText", pset.HSet)
            if result is False:
                raise RuntimeError("InsertText 액션이 실패했습니다.")
            return
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                wait_for_hwp_com(attempt)

    preview = str(chunk).replace("\n", " ")[:40]
    raise RuntimeError(
        f"HWP 본문 삽입을 4회 시도했지만 실패했습니다: {preview!r} / {last_error}"
    ) from last_error


def insert_plain_text(hwp, text):
    text = normalize_text(text)
    if not text:
        return
    parts = text.split("\n")
    for index, part in enumerate(parts):
        if part:
            for chunk in split_text_chunks(part):
                insert_text_chunk(hwp, chunk)
        if index < len(parts) - 1:
            run_action(hwp, "BreakLine")


def try_create_one_cell_table(hwp, width_mm=68, min_height_mm=12):
    try:
        pset = hwp.HParameterSet.HTableCreation
        hwp.HAction.GetDefault("TableCreate", pset.HSet)
        pset.Rows = 1
        pset.Cols = 1
        pset.WidthType = 2
        pset.HeightType = 1
        try:
            pset.CreateItemArray("ColWidth", 1)
            pset.ColWidth.SetItem(0, mm_to_hwp_unit(hwp, width_mm))
        except Exception:
            pass
        try:
            pset.CreateItemArray("RowHeight", 1)
            pset.RowHeight.SetItem(0, mm_to_hwp_unit(hwp, min_height_mm))
        except Exception:
            pass
        result = hwp.HAction.Execute("TableCreate", pset.HSet)
        return result is not False
    except Exception:
        return False


def leave_table_cell(hwp):
    run_action(hwp, "MoveLineEnd")
    if not run_action(hwp, "MoveRight"):
        run_action(hwp, "Cancel")
    run_action(hwp, "BreakPara")


def insert_token_sequence(hwp, tokens, input_path=None):
    for token in tokens:
        if token.kind == "text":
            insert_plain_text(hwp, token.value)
        elif token.kind == "formula":
            insert_equation(hwp, token.value)
        elif token.kind == "image" and input_path is not None:
            insert_picture(hwp, get_image_path(input_path, token.number))
        elif token.kind == "choice_box":
            insert_choice_box(hwp, token.value)


def insert_choice_box(hwp, text):
    content = normalize_text(str(text or "").strip())
    if not content:
        return

    run_action(hwp, "BreakPara")
    if try_create_one_cell_table(hwp):
        insert_token_sequence(hwp, parse_tokens(content))
        leave_table_cell(hwp)
        return

    insert_plain_text(hwp, "┌ 보기 ┐")
    run_action(hwp, "BreakLine")
    insert_token_sequence(hwp, parse_tokens(content))
    run_action(hwp, "BreakLine")
    insert_plain_text(hwp, "└────┘")
    run_action(hwp, "BreakPara")


def insert_equation_object(hwp, formula):
    equation = convert_formula_to_hwp(formula)
    if not equation:
        return
    try:
        action = hwp.CreateAction("EquationCreate")
        param = action.CreateSet()
        action.GetDefault(param)
        param.SetItem("String", equation)
        action.Execute(param)
    except Exception:
        try:
            pset = hwp.HParameterSet.HEqEdit
            hwp.HAction.GetDefault("EquationCreate", pset.HSet)
            pset.string = equation
            hwp.HAction.Execute("EquationCreate", pset.HSet)
        except Exception:
            insert_plain_text(hwp, "[수식: " + str(formula or "").strip() + "]")


def split_equation_chain(formula):
    text = str(formula or "").strip()
    if "," in text or "，" in text:
        return [text]
    if text.count("=") < 2:
        return [text]
    parts = [part.strip() for part in re.split(r"(?<![<>!])=(?!=)", text)]
    return parts if len(parts) > 1 and all(parts) else [text]


def split_equation_sequence(formula):
    text = str(formula or "").strip()
    if "," not in text and "，" not in text:
        return [("formula", text)]
    pieces = [piece.strip() for piece in re.split(r"\s*[,，]\s*", text) if piece.strip()]
    equation_piece_count = sum(
        1 for piece in pieces
        if re.search(r"(?<![<>!])=(?!=)", piece)
    )
    if equation_piece_count < 2:
        return [("formula", text)]
    sequence = []
    for index, piece in enumerate(pieces):
        if index:
            sequence.append(("text", ", "))
        sequence.append(("formula", piece))
    return sequence


def insert_equation_chain(hwp, formula):
    for index, part in enumerate(split_equation_chain(formula)):
        if index:
            insert_plain_text(hwp, " = ")
        insert_equation_object(hwp, part)


def insert_equation(hwp, formula):
    for kind, value in split_equation_sequence(formula):
        if kind == "text":
            insert_plain_text(hwp, value)
        else:
            insert_equation_chain(hwp, value)


def mm_to_hwp_unit(hwp, mm):
    try:
        return int(hwp.MiliToHwpUnit(float(mm)))
    except Exception:
        return int(float(mm) * 283.465)


def get_image_aspect_ratio(image_path):
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            width, height = image.size
            if width > 0 and height > 0:
                return height / width
    except Exception:
        pass
    return 0.75


def prepare_picture_for_hwp(image_path):
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            prepared = image.copy()
            max_width_px = 350
            if prepared.width > max_width_px:
                height = max(1, round(prepared.height * max_width_px / prepared.width))
                prepared = prepared.resize((max_width_px, height), Image.LANCZOS)
            temp_path = Path(tempfile.gettempdir()) / (
                f"hwp_picture_{os.getpid()}_{time.time_ns()}_{image_path.name}"
            )
            prepared.save(temp_path)
            return temp_path
    except Exception:
        return image_path


def resize_selected_picture_to_column(hwp, image_path, max_width_mm=68):
    width_unit = mm_to_hwp_unit(hwp, max_width_mm)
    height_unit = mm_to_hwp_unit(hwp, max_width_mm * get_image_aspect_ratio(image_path))
    try:
        pset = hwp.HParameterSet.HShapeObject
        hwp.HAction.GetDefault("ShapeObjDialog", pset.HSet)
        try:
            pset.Width = width_unit
            pset.Height = height_unit
            pset.TreatAsChar = 1
        except Exception:
            pass
        try:
            pset.HSet.SetItem("Width", width_unit)
            pset.HSet.SetItem("Height", height_unit)
            pset.HSet.SetItem("TreatAsChar", 1)
        except Exception:
            pass
        hwp.HAction.Execute("ShapeObjDialog", pset.HSet)
    except Exception:
        pass


def insert_picture(hwp, image_path):
    if not image_path:
        insert_plain_text(hwp, "[이미지 파일 없음]")
        return

    prepared_path = prepare_picture_for_hwp(image_path)
    try:
        hwp.InsertPicture(
            str(prepared_path.resolve()), True, 0, False, False, 0, 0, 0
        )
        resize_selected_picture_to_column(hwp, image_path)
    except Exception:
        # 일부 한글 버전은 그림 삽입을 마친 뒤 선택 개체를 정리하면서
        # 예외를 발생시킨다. 재시도하면 그림이 중복되므로 완료로 처리한다.
        return
    finally:
        if prepared_path != image_path:
            try:
                prepared_path.unlink()
            except Exception:
                pass


def insert_generated_content(hwp, input_path, text, fallback_image_number=1):
    tokens, next_image_number = parse_tokens_from_number(
        text, fallback_image_number
    )
    insert_token_sequence(hwp, tokens, input_path=input_path)
    return next_image_number


def run_required_action(hwp, action_name, error_message):
    try:
        result = hwp.HAction.Run(action_name)
    except Exception as exc:
        raise RuntimeError(f"{error_message} 한글 액션: {action_name} / {exc}") from exc
    if result is False and not wait_for_hwp_document_path(hwp, path):
        raise RuntimeError(f"{error_message} 한글 액션: {action_name}")


def insert_questions_with_endnotes(hwp, input_path, questions, answers):
    question_sections = split_numbered_sections(questions, "문제 본문")
    answer_sections = dict(split_numbered_sections(answers, "정답 및 해설"))
    question_numbers = {number for number, _ in question_sections}
    missing_answers = [
        number for number, _ in question_sections if number not in answer_sections
    ]
    extra_answers = sorted(set(answer_sections) - question_numbers)
    if missing_answers:
        raise ValueError(
            "다음 문항의 정답·해설을 찾지 못했습니다: "
            + ", ".join(f"문항{number}" for number in missing_answers)
        )
    if extra_answers:
        raise ValueError(
            "문제 본문에 없는 정답·해설이 있습니다: "
            + ", ".join(f"문항{number}" for number in extra_answers)
        )

    next_image_number = 1
    for index, (number, question_body) in enumerate(question_sections):
        # The template owns the visible automatic numbering ("1.", "2.", ...).
        # At each numbered paragraph, create the endnote first so its marker
        # appears immediately after the number, then return and insert the
        # corresponding question body.
        run_required_action(
            hwp,
            "InsertEndnote",
            f"문항{number}의 미주를 만들지 못했습니다.",
        )
        insert_generated_content(
            hwp,
            input_path,
            answer_sections[number],
            fallback_image_number=next_image_number,
        )
        run_required_action(
            hwp,
            "CloseEx",
            f"문항{number}의 미주 편집을 끝내지 못했습니다.",
        )
        next_image_number = insert_generated_content(
            hwp,
            input_path,
            question_body,
            fallback_image_number=next_image_number,
        )
        if index < len(question_sections) - 1:
            run_action(hwp, "BreakPara")


def configure_find_replace(hwp, find_text):
    pset = hwp.HParameterSet.HFindReplace
    hwp.HAction.GetDefault("RepeatFind", pset.HSet)
    pset.FindString = find_text
    pset.Direction = 0
    pset.IgnoreMessage = 1
    pset.MatchCase = 1
    pset.AllWordForms = 0
    pset.SeveralWords = 0
    pset.UseWildCards = 0
    pset.WholeWordOnly = 0
    pset.FindRegExp = 0
    pset.FindType = 1
    return pset


def find_and_select(hwp, text):
    run_action(hwp, "MoveDocBegin")
    pset = configure_find_replace(hwp, text)
    try:
        return bool(hwp.HAction.Execute("RepeatFind", pset.HSet))
    except Exception as exc:
        raise TemplateError(f"템플릿 토큰 검색 중 오류가 발생했습니다: {text} / {exc}") from exc


def validate_template_tokens(hwp):
    missing = [token for token in REQUIRED_TEMPLATE_TOKENS if not find_and_select(hwp, token)]
    if missing:
        raise TemplateError(
            "템플릿에서 다음 치환 토큰을 찾지 못했습니다: "
            + ", ".join(missing)
            + ". template.hwp에 토큰을 정확히 입력해 주세요."
        )


def replace_token_with_text(hwp, token, value):
    if not find_and_select(hwp, token):
        raise TemplateError(f"템플릿 토큰을 찾지 못했습니다: {token}")
    insert_plain_text(hwp, value)


def replace_token_with_generated_content(hwp, token, input_path, value):
    if not find_and_select(hwp, token):
        raise TemplateError(f"템플릿 토큰을 찾지 못했습니다: {token}")
    insert_generated_content(hwp, input_path, value)


def delete_selected_token(hwp, token):
    if not find_and_select(hwp, token):
        raise TemplateError(f"템플릿 토큰을 찾지 못했습니다: {token}")
    run_required_action(hwp, "Delete", f"템플릿 토큰을 삭제하지 못했습니다: {token}")


def replace_questions_with_endnotes(hwp, input_path, content):
    if not find_and_select(hwp, "{{QUESTIONS}}"):
        raise TemplateError("템플릿 토큰을 찾지 못했습니다: {{QUESTIONS}}")
    run_required_action(
        hwp,
        "Delete",
        "템플릿의 {{QUESTIONS}} 토큰을 삭제하지 못했습니다.",
    )
    insert_questions_with_endnotes(
        hwp,
        input_path,
        content.questions,
        content.answers,
    )


def wait_for_hwp_document_path(hwp, path, timeout=8):
    expected = str(path.resolve()).lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        hide_hwp_window(hwp)
        for getter in (
            lambda: hwp.Path,
            lambda: hwp.XHwpDocuments.Item(0).FullName,
            lambda: hwp.XHwpDocuments.Item(0).Path,
        ):
            try:
                current = str(getter() or "").lower()
            except Exception:
                continue
            if current and (current == expected or current.endswith(path.name.lower())):
                return True
        time.sleep(0.2)
    return False


def open_hwp(hwp, path):
    hide_hwp_window(hwp)
    try:
        result = hwp.Open(str(path.resolve()), "HWP", "forceopen:true")
    except Exception:
        result = hwp.Open(str(path.resolve()))
    if result is False:
        raise TemplateError(f"HWP 템플릿 복사본을 열지 못했습니다: {path}")


    hide_hwp_window(hwp)


def save_hwp(hwp, output_path):
    output = str(output_path.resolve())
    try:
        hwp.SaveAs(output, "HWP", "")
    except Exception:
        hwp.SaveAs(output)


def build_hwp_from_template(input_path, template_path, output_path, content):
    if not template_path.exists():
        raise FileNotFoundError(f"템플릿 파일을 찾을 수 없습니다: {template_path}")
    if template_path.suffix.lower() != ".hwp":
        raise TemplateError(f"템플릿은 .hwp 파일이어야 합니다: {template_path}")
    if template_path.resolve() == output_path.resolve():
        raise TemplateError("출력 파일 경로는 원본 템플릿 경로와 달라야 합니다.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, output_path)
    hwp = None
    stop_security_watcher = start_hwp_security_dialog_watcher()
    error_log_path = input_path.with_name(input_path.stem + "_hwp_error.log")
    build_error = None
    try:
        hwp = dispatch_hwp()
        open_hwp(hwp, output_path)
        hide_hwp_window(hwp)
        validate_template_tokens(hwp)

        replace_questions_with_endnotes(hwp, input_path, content)
        hide_hwp_window(hwp)
        save_hwp(hwp, output_path)
        hide_hwp_window(hwp)
    except Exception as exc:
        build_error = exc
        raise
    finally:
        stop_security_watcher()
        if hwp is not None:
            try:
                hwp.Quit()
            except Exception:
                pass
        if build_error is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
            log_text = (
                "HWP 템플릿 생성 오류\n"
                f"입력 파일: {input_path}\n"
                f"템플릿 파일: {template_path}\n"
                f"출력 예정 파일: {output_path}\n"
                f"오류: {build_error}\n"
            )
            try:
                error_log_path.write_text(log_text, encoding="utf-8")
            except OSError:
                fallback_log = output_path.with_name(
                    output_path.stem + "_hwp_error.log"
                )
                try:
                    fallback_log.write_text(log_text, encoding="utf-8")
                except OSError:
                    pass
    return output_path


def open_completed_hwp(path):
    if not sys.platform.startswith("win"):
        return
    try:
        os.startfile(str(path.resolve()))
    except OSError as exc:
        raise RuntimeError(
            f"문제지는 생성했지만 완성 파일을 열지 못했습니다: {path}\n{exc}"
        ) from exc


def main():
    parser = argparse.ArgumentParser(
        description="학원 HWP 템플릿에 생성 문제와 답안을 삽입합니다."
    )
    parser.add_argument("input", nargs="?", help="생성된 문제 텍스트 파일")
    parser.add_argument("-t", "--template", default="", help="원본 template.hwp 경로")
    parser.add_argument("-o", "--output", default="", help="출력 HWP 경로")
    parser.add_argument(
        "--student-name",
        default="",
        help="출력 파일명에 사용할 학생 이름",
    )
    parser.add_argument(
        "--date",
        default="",
        help="출력 파일명에 사용할 날짜(YYYYMMDD). 기본값은 오늘",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="생성 완료 후 결과 HWP를 자동으로 열지 않음",
    )
    args = parser.parse_args()

    input_text = args.input or select_input_file()
    if not input_text:
        show_message("HWP 생성기", "입력 파일 선택을 취소했습니다.")
        return 1

    input_path = Path(input_text).expanduser().resolve()
    if not input_path.exists():
        show_message(
            "HWP 생성 오류",
            f"입력 파일을 찾을 수 없습니다:\n{input_path}",
            is_error=True,
        )
        return 1

    template_text = args.template or select_template_file(input_path.parent)
    if not template_text:
        show_message("HWP 생성기", "템플릿 파일 선택을 취소했습니다.")
        return 1

    template_path = Path(template_text).expanduser().resolve()
    if not template_path.exists():
        show_message(
            "HWP 생성 오류",
            f"템플릿 파일을 찾을 수 없습니다:\n{template_path}",
            is_error=True,
        )
        return 1

    try:
        source_text = input_path.read_text(encoding="utf-8")
        content = parse_document_content(
            source_text,
            input_path,
            student_override=args.student_name,
            date_override=args.date,
        )
        output_path = (
            Path(args.output).expanduser().resolve()
            if args.output
            else default_output_path(input_path, content)
        )
        result = build_hwp_from_template(
            input_path, template_path, output_path, content
        )
    except Exception as exc:
        show_message(
            "HWP 생성 실패",
            f"HWP 생성에 실패했습니다.\n\n{exc}\n\n"
            "같은 폴더의 *_hwp_error.log 파일에서 자세한 내용을 확인할 수 있습니다.",
            is_error=True,
        )
        return 1

    if not args.no_open:
        try:
            open_completed_hwp(result)
        except Exception as exc:
            show_message(
                "HWP 생성 완료",
                f"문제지는 만들었지만 자동으로 열지 못했습니다.\n\n{result}\n\n{exc}",
                is_error=True,
            )
            return 0
    if args.no_open:
        print(result, flush=True)
        return 0
    show_message("HWP 생성 완료", f"문제지를 만들었습니다.\n\n{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
