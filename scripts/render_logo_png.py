from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(r"E:\大学课程\竞赛\时察千机项目标志.png")
JPG_OUT = Path(r"E:\大学课程\竞赛\shicha-qianji-logo.jpg")
SIZE = 1024
SCALE = SIZE / 256


def point(x, y):
    return round(x * SCALE), round(y * SCALE)


def cubic(p0, p1, p2, p3, steps=48):
    points = []
    for index in range(steps + 1):
        t = index / steps
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        points.append(point(x, y))
    return points


def draw_curve(draw, segments, color, width):
    points = []
    for segment in segments:
        curve = cubic(*segment)
        points.extend(curve if not points else curve[1:])
    draw.line(points, fill=color, width=round(width * SCALE), joint="curve")
    radius = width * SCALE / 2
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def build():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((point(12, 12), point(244, 244)), radius=round(54 * SCALE), fill="#20363B")

    draw_curve(
        draw,
        [
            ((45, 153), (61, 152), (68, 125), (88, 125)),
            ((88, 125), (107, 125), (109, 165), (130, 165)),
            ((130, 165), (148, 165), (151, 98), (173, 98)),
            ((173, 98), (186, 98), (193, 115), (211, 116)),
        ],
        "#74C7BB",
        13,
    )
    draw_curve(
        draw,
        [
            ((45, 181), (61, 180), (74, 163), (90, 163)),
            ((90, 163), (108, 163), (113, 182), (130, 182)),
            ((130, 182), (147, 182), (155, 160), (172, 160)),
            ((172, 160), (184, 160), (192, 168), (211, 169)),
        ],
        "#B7E2D8",
        5,
    )

    center = point(130, 165)
    radius = 21 * SCALE
    draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill="#20363B", outline="#F0B45A", width=round(9 * SCALE))
    check = [point(120, 165), point(127, 172), point(142, 155)]
    draw.line(check, fill="#F0B45A", width=round(7 * SCALE), joint="curve")
    check_radius = 3.5 * SCALE
    for x, y in (check[0], check[-1]):
        draw.ellipse((x - check_radius, y - check_radius, x + check_radius, y + check_radius), fill="#F0B45A")

    draw_curve(draw, [((69, 71), (98, 48), (141, 41), (178, 57)), ((178, 57), (193, 63), (204, 72), (215, 84))], "#4B9B99", 6)
    arrow = [point(203, 72), point(215, 84), point(198, 86)]
    draw.line(arrow, fill="#4B9B99", width=round(6 * SCALE), joint="curve")
    tip_radius = 3 * SCALE
    for x, y in (arrow[0], arrow[-1]):
        draw.ellipse((x - tip_radius, y - tip_radius, x + tip_radius, y + tip_radius), fill="#4B9B99")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, format="PNG", optimize=True)
    # Some older upload controls reject transparent RGBA PNGs; provide a standard RGB fallback.
    jpg = Image.new("RGB", image.size, "white")
    jpg.paste(image, mask=image.getchannel("A"))
    jpg.save(JPG_OUT, format="JPEG", quality=95, optimize=True, progressive=False)
    print(OUT)
    print(JPG_OUT)


if __name__ == "__main__":
    build()
