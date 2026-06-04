# Making Team

Google Sheets Apps Script and local Windows tools for math-test automation.

## Main Files

- `Code.gs` / `master_code.gs`: master Apps Script for problem-bank analysis, student reports, similar-problem generation, token logging, and image prompt rules.
- `teacher_code.gs`: Apps Script for teacher-side spreadsheet integration.
- `math_diagram_renderer.py`: local renderer for `[IMAGE_PROMPT번호: ...]` math diagrams.
- `수학도표렌더러.exe`: Windows executable build of the diagram renderer.
- `hwp_problem_builder.py`: local HWP builder that merges generated text and numbered diagram images.
- `HWP문항생성기.exe`: Windows executable build of the HWP builder.
- `TEMPLATE_NOTES.md`: collected workbook-style image templates and implementation backlog.

## Implemented Diagram Templates

- `parabola_band_area`
- `parabola_basic_shape`
- `parabola_xintercepts_vertex_triangle`
- `parabola_xintercepts_yintercept_triangle`
- `parabola_yintercept_vertex_xintercept_triangle`

See `TEMPLATE_NOTES.md` for more planned geometry and function templates.

## Notes

The renderer is intended to receive concrete `IMAGE_PROMPT` blocks. For function graphs, unresolved expressions such as `y = g(x)` or `points=given points` should be rejected at prompt/validation time. Geometry templates may allow unknown length labels such as `x` when the unknown is part of the problem.

