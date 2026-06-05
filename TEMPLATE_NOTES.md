# Math Image Template Notes

This file preserves the template ideas collected from workbook-style math problem images.
Only some templates are implemented in `math_diagram_renderer.py`; the rest are backlog candidates.

## Current Implementation Status

Implemented in `math_diagram_renderer.py`:

- `parabola_band_area`
- `parabola_basic_shape`
- `parabola_xintercepts_vertex_triangle`
- `parabola_x_intercepts_vertex_triangle` alias
- `parabola_xintercepts_yintercept_triangle`
- `parabola_x_intercepts_y_intercept_triangle` alias
- `parabola_yintercept_vertex_xintercept_triangle`
- `parabola_y_intercept_vertex_x_intercept_triangle` alias
- `two_origin_parabolas_horizontal_line`
- `two_origin_parabolas_vertical_line_ratio`
- `two_parabolas_between_area`
- `parabola_family_origin`

Implemented behavior:

- Coordinate axes use arrowheads.
- Grid and tick marks are hidden.
- Origin `O`, `x`, `y` are shown.
- Numeric labels are kept close to axes.
- For triangle parabola templates, x-axis labels are placed below the axis to avoid A/B point labels.
- If the vertex is on the y-axis, the vertex y-value is shifted away from the curve.
- Duplicate y labels are removed when y-intercept and vertex coincide.
- Long decimal labels are shortened or converted to small-denominator fractions.

## Prompt Rules To Preserve

Function graph templates:

- Do not leave unresolved symbols in `equation`.
- Bad examples: `y = g(x)`, `y = f(x)`, `y = k(x-alpha)(x-beta)`, `points=given points`.
- Use concrete equations such as `y = x^2 - 16`.
- For supported templates, give `equation` only and let the renderer calculate intercepts, vertex, points, and shaded regions.

Geometry templates:

- Unknown lengths such as `x` are allowed when they are labels for a length, width, radius, or cut size.
- This is different from function `equation`, where unresolved coefficients should be rejected.

Image prompt format:

```text
[이미지 필요7:
종류=좌표평면
식=y = x² - 16
표시=x축 교점 A,B, 꼭짓점 C, 삼각형 ABC
]
[IMAGE_PROMPT7:
template=parabola_xintercepts_vertex_triangle
equation=y = x^2 - 16
]
```

## Implemented Function Templates

### `parabola_basic_shape`

Purpose:

- Basic parabola diagram for sign/position/equation reading problems.
- Shows vertex, x-intercepts, and y-intercept when available.

Fields:

```text
template=parabola_basic_shape
equation=y = -x^2 + 6*x - 7
show_vertex=true
show_x_intercepts=true
show_y_intercept=true
```

Notes:

- `show_*` fields are optional.
- Useful for problems asking signs of `a, b, c`, vertex coordinates, or reading a point from a graph.

### `parabola_xintercepts_vertex_triangle`

Purpose:

- A/B are x-axis intercepts.
- C is vertex.
- Triangle ABC is shaded.

Fields:

```text
template=parabola_xintercepts_vertex_triangle
equation=y = x^2 - 16
```

Typical source images:

- Parabola intersects x-axis at A and B, vertex C, ask area of triangle ABC.
- Same structure for upward/downward parabolas.

### `parabola_xintercepts_yintercept_triangle`

Purpose:

- A/B are x-axis intercepts.
- C is y-axis intercept.
- Triangle ABC is shaded.

Fields:

```text
template=parabola_xintercepts_yintercept_triangle
equation=y = -x^2 + 4*x + 5
```

Typical source images:

- Parabola intersects x-axis at A and B and y-axis at C.
- Ask area of triangle ABC or infer coefficients from area.

### `parabola_yintercept_vertex_xintercept_triangle`

Purpose:

- A is y-axis intercept.
- B is vertex.
- C is selected x-axis intercept.
- Triangle ABC is shaded.

Fields:

```text
template=parabola_yintercept_vertex_xintercept_triangle
equation=y = 1/2*x^2 - 2*x - 6
x_intercept=positive
```

Allowed `x_intercept`:

- `positive`
- `negative`
- `left`
- `right`

Typical source images:

- y-axis intercept A, vertex B, positive/negative x-axis intercept C.
- Ask area of triangle ABC.

### `parabola_band_area`

Purpose:

- Two function graphs and two vertical lines enclose a band area.
- Renderer chooses range, shades between curves, and labels nonzero vertical boundaries.

Fields:

```text
template=parabola_band_area
equation_top=y = x^2 + 2
equation_bottom=y = x^2 - 3
x_left=1
x_right=4
```

Notes:

- If `x_left=0` or `x_right=0`, the y-axis is used as the boundary and no red dashed line is drawn on top of it.
- Do not include `type`, `equation`, `x_range`, `y_range`, `region`, or `labels` for this template.

## Function Template Backlog

### `parabola_family_origin`

Purpose:

- Several parabolas through the origin, often labeled a, b, c, d, e.
- Used in "match graph to equation" problems.

Fields:

```text
template=parabola_family_origin
equations=y=2/5*x^2, y=x^2, y=-x^2
curve_labels=a,b,c
```

### `multiple_choice_parabola_position`

Purpose:

- Five small coordinate-plane choices.
- Used for selecting the graph matching conditions such as `a>0, p<0, q>0`.

Fields:

```text
template=multiple_choice_parabola_position
choices=5
condition=a>0,p<0,q>0
```

Notes:

- This is likely useful but needs mini-plot layout support.

### `parabola_shift_from_base`

Purpose:

- Show a parabola translated from a base graph such as `y=2x^2`.

Fields:

```text
template=parabola_shift_from_base
base_equation=y = 2*x^2
shift_x=0
shift_y=-2
```

### `two_parabolas_same_width_horizontal_chord`

Purpose:

- Two equal-width parabolas.
- Points A and B on the two graphs are connected by a horizontal segment.

Fields:

```text
template=two_parabolas_same_width_horizontal_chord
equation_left=y=1/2*(x+4)^2
equation_right=y=1/2*(x-3)^2
chord_y=
points=A,B
```

### `two_origin_parabolas_horizontal_line`

Purpose:

- Two parabolas through origin and a horizontal line `y=k`.
- Intersections P, Q, R on the line.

Fields:

```text
template=two_origin_parabolas_horizontal_line
equation_left=y=x^2
equation_right=y=a*x^2
horizontal_y=4
points=P,Q,R
condition=PQ=QR
```

### `two_origin_parabolas_vertical_line_ratio`

Purpose:

- Two parabolas through origin and a vertical line.
- Intersections create segment ratio such as `AB:BC=1:3`.

Fields:

```text
template=two_origin_parabolas_vertical_line_ratio
equation1=y=1/3*x^2
equation2=y=a*x^2
vertical_x=1
ratio=AB:BC
```

### `two_origin_parabolas_parallelogram`

Purpose:

- Points on two origin parabolas and y-axis form a parallelogram.

Fields:

```text
template=two_origin_parabolas_parallelogram
equation1=y=1/3*x^2
equation2=y=x^2
shape=parallelogram
points=A,B,C,D
```

### `parabola_diamond_on_axes`

Purpose:

- Parabola with points A, O, B, C forming a square/rhombus.

Fields:

```text
template=parabola_diamond_on_axes
equation=y=1/4*x^2
points=A,O,B,C
shape=diamond
shade=diamond
```

### `two_parabolas_between_area`

Purpose:

- Two parabolas enclose a lens/leaf-like shaded area.

Fields:

```text
template=two_parabolas_between_area
equation_left=y=-x^2-4*x
equation_right=y=-x^2+2*x+3
shade=between
```

### `two_parabolas_square`

Purpose:

- Two parabolas, often one up and one down, with four points forming a square/rectangle.

Fields:

```text
template=two_parabolas_square
equation_top=y=x^2
equation_bottom=y=-1/2*x^2
shape=square
```

### `two_parabolas_shared_vertex_intersections`

Purpose:

- Intersections of two parabolas are the vertex points of each graph.

Fields:

```text
template=two_parabolas_shared_vertex_intersections
equation1=y=x^2-9
equation2=y=a*(x-p)^2
```

### `line_to_parabola_quadrant_match`

Purpose:

- Given a line graph, select or infer a related parabola graph and quadrant behavior.

Fields:

```text
template=line_to_parabola_quadrant_match
line_equation=y=a*x+b
parabola_form=y=-(x+a)^2+b
```

## Quadratic Equation / Geometry Backlog

### `annulus_area`

Purpose:

- Concentric circles or ring area problems.

Fields:

```text
template=annulus_area
outer_radius=
inner_radius=
radius_gap=
shade=inner|ring
```

### `circle_with_two_semicircles`

Purpose:

- Large circle with two inner semicircles/circles and shaded remaining area.

Fields:

```text
template=circle_with_two_semicircles
outer_diameter=
left_inner_diameter=
right_inner_diameter=
shade=remaining
```

### `rectangle_point_triangle`

Purpose:

- Rectangle ABCD with points P and Q on sides; triangle PBQ area condition.

Fields:

```text
template=rectangle_point_triangle
width=
height=
point_top_distance=
point_right_distance=
triangle_points=P,B,Q
```

### `rectangle_cross_road`

Purpose:

- Rectangular field with horizontal/vertical roads of equal width.

Fields:

```text
template=rectangle_cross_road
width=40
height=30
road_width=x
shade=fields
```

### `rectangle_slanted_cross_road`

Purpose:

- Rectangular field with slanted crossing roads.

Fields:

```text
template=rectangle_slanted_cross_road
width=25
height=20
road_width=x
shade=fields
```

### `rectangle_multi_slanted_roads`

Purpose:

- Rectangle with several slanted roads.

Fields:

```text
template=rectangle_multi_slanted_roads
width=50
height=40
road_width=x
road_count=3
shade=remaining_land
```

### `square_expanded_garden`

Purpose:

- Square garden expanded right/down into a rectangle.

Fields:

```text
template=square_expanded_garden
inner_side=x
expand_right=9
expand_bottom=6
shade_inner=true
```

### `rectangular_park_border`

Purpose:

- Rectangular park surrounded by a uniform walkway/border.

Fields:

```text
template=rectangular_park_border
inner_width=x
inner_height=x+12
border_width=6
shade=inner_park
```

### `rectangle_diagonal_flower_path`

Purpose:

- Rectangle flowerbed split by a diagonal/parallelogram path.

Fields:

```text
template=rectangle_diagonal_flower_path
width_ratio=2
height_ratio=1
path_width=2
shade=flowerbeds
```

### `two_squares_on_segment`

Purpose:

- A segment is divided into two parts, and each part forms a square.

Fields:

```text
template=two_squares_on_segment
total_length=11
left_side=x
right_side=11-x
layout=side_by_side
```

### `growing_rectangle`

Purpose:

- Rectangle width/height changes over time.

Fields:

```text
template=growing_rectangle
initial_width=30
initial_height=24
width_change_per_time=-2
height_change_per_time=3
time_label=x
```

### `open_box_net_equal_cuts`

Purpose:

- Square paper with equal corner cuts, folded into an open box.

Fields:

```text
template=open_box_net_equal_cuts
paper_shape=square
paper_side=10
cut_side=x
shade=box_faces
```

### `open_box_net_rectangular_paper`

Purpose:

- Rectangular paper with equal corner cuts.

Fields:

```text
template=open_box_net_rectangular_paper
paper_width=x+6
paper_height=x
cut_side=3
shade=box_faces
```

### `folded_tray`

Purpose:

- Sheet/tray with both sides folded up to make a water trough.

Fields:

```text
template=folded_tray
sheet_width=40
fold_height=x
bottom_area=168
side_shape=slanted
```

### `adjacent_rectangles`

Purpose:

- Square and rectangle attached side by side, often sharing a side.

Fields:

```text
template=adjacent_rectangles
left_width=
left_height=
right_width=
right_height=
shared_height=
```

### `two_squares_from_segment`

Purpose:

- Segment divided into two parts, each made into a square, area sum condition.

Fields:

```text
template=two_squares_from_segment
total_length=8
left_square_side=x
right_square_side=8-x
condition=sum_area
```

### `moving_points_rectangle_triangle`

Purpose:

- Rectangle with points moving along sides, forming a triangle of given area.

Fields:

```text
template=moving_points_rectangle_triangle
rectangle_width=10
rectangle_height=15
point_p_speed=1
point_q_speed=2
shade=triangle_PCQ
```

### `right_isosceles_triangle_inner_rectangle`

Purpose:

- Right isosceles triangle containing an inner rectangle.

Fields:

```text
template=right_isosceles_triangle_inner_rectangle
leg=8
inner_rectangle_area=8
shade=rectangle_PQCR
```

### `right_isosceles_triangle_parallelogram`

Purpose:

- Right isosceles triangle containing an inner parallelogram.

Fields:

```text
template=right_isosceles_triangle_parallelogram
leg=14
parallelogram_area=48
shade=parallelogram_ADEF
```

### `tiled_rectangle_corner_square`

Purpose:

- Tiled rectangle with a small corner square highlighted.

Fields:

```text
template=tiled_rectangle_corner_square
tile_rows=
tile_cols=
small_square_side=4
highlight_corner=true
```

## Suggested Implementation Priority

Function templates:

1. `parabola_basic_shape` - done
2. `parabola_xintercepts_vertex_triangle` - done
3. `parabola_xintercepts_yintercept_triangle` - done
4. `parabola_yintercept_vertex_xintercept_triangle` - done
5. `two_parabolas_vertical_band` / existing `parabola_band_area` - partially done
6. `two_origin_parabolas_horizontal_line`
7. `two_origin_parabolas_vertical_line_ratio`
8. `multiple_choice_parabola_position`

Geometry templates:

1. `rectangular_park_border`
2. `rectangle_cross_road`
3. `rectangle_slanted_cross_road`
4. `open_box_net_equal_cuts`
5. `open_box_net_rectangular_paper`
6. `two_squares_on_segment`
7. `folded_tray`
8. `right_isosceles_triangle_inner_rectangle`
9. `moving_points_rectangle_triangle`
