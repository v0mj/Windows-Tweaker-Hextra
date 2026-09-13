"""Hextra design system.

Central place for the visual language of the Hextra shell: colour roles,
typography, spacing, surfaces and the small reusable widgets (page headers,
cards, pills, stat tiles, icons, empty states) that every page composes.

Keeping this in one module means pages stay consistent with each other and a
visual change only has to happen once.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import Qt, QRectF, QPointF, QSize
from PyQt6.QtGui import (
    QColor,
    QBrush,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QLinearGradient,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Palette roles
# ---------------------------------------------------------------------------

BG = "#020617"
SURFACE_TOP = "#081226"
SURFACE_BOTTOM = "#020617"

TEXT = "#e6f3ff"
TEXT_MUTED = "#93a7bd"
TEXT_DIM = "#5f7186"

LINE = "#16233c"
LINE_SOFT = "#0d1729"

OK = "#34d399"
WARN = "#fbbf24"
ERR = "#fb4d6d"
INFO = "#38bdf8"

UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"

# type scale (px)
T_DISPLAY = 26
T_TITLE = 17
T_BODY = 13
T_SMALL = 12
T_EYEBROW = 10
T_MONO = 10.5

# spacing scale
S_1 = 4
S_2 = 8
S_3 = 12
S_4 = 16
S_5 = 24
S_6 = 32

# radii
R_CARD = 16
R_ROW = 12
R_CTRL = 10
R_PILL = 999


def rgba(color, alpha):
    c = QColor(color)
    a = max(0, min(255, int(alpha)))
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"


def mix(color_a, color_b, t):
    a, b = QColor(color_a), QColor(color_b)
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    ).name()


def accent(accent=None):
    c = QColor(accent or INFO)
    return c.name() if c.isValid() else INFO


def accent_secondary(color=None):
    c = QColor(accent(color))
    h, s, l, _ = c.getHslF()
    return QColor.fromHslF((h + 0.10) % 1.0, min(1.0, max(0.35, s)), min(0.74, max(0.46, l * 1.05))).name()


TONES = {
    "ok": OK,
    "warn": WARN,
    "err": ERR,
    "info": INFO,
    "accent": None,  # resolved at call time
    "muted": TEXT_MUTED,
}


def tone_color(tone, accent_color=None):
    if tone == "accent":
        return accent(accent_color)
    return TONES.get(tone, TEXT_MUTED)


# ---------------------------------------------------------------------------
# Surfaces & controls (QSS)
# ---------------------------------------------------------------------------

def card_qss(accent_color=None, radius=R_CARD, alpha=196, hover=False):
    ac = accent(accent_color)
    edge = rgba(ac, 40 if hover else 26)
    return (
        "QFrame{"
        f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {rgba(SURFACE_TOP, alpha)},stop:1 {rgba(SURFACE_BOTTOM, int(alpha * 0.86))});"
        f"border:1px solid {edge};"
        f"border-radius:{radius}px;"
        "}"
    )


def card_hover_qss(accent_color=None, radius=R_CARD):
    ac = accent(accent_color)
    return (
        card_qss(accent_color, radius)
        + "QFrame:hover{"
        f"border:1px solid {rgba(ac, 66)};"
        "}"
    )


def input_qss(accent_color=None, height=38):
    ac = accent(accent_color)
    return (
        "QLineEdit{"
        f"background:{rgba('#01040f', 168)};"
        f"color:{TEXT};"
        f"border:1px solid {rgba(ac, 34)};"
        f"border-radius:{R_CTRL}px;"
        f"font-family:'{UI_FONT}';font-size:{T_BODY}px;font-weight:500;"
        "padding:0 14px;"
        f"selection-background-color:{rgba(ac, 92)};"
        "}"
        "QLineEdit:hover{"
        f"border-color:{rgba(ac, 70)};"
        "}"
        "QLineEdit:focus{"
        f"border-color:{ac};"
        f"background:{rgba('#01040f', 210)};"
        "}"
    )


def button_qss(primary=False, accent_color=None, danger=False):
    ac = ERR if danger else accent(accent_color)
    if primary:
        return (
            "QPushButton{"
            f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {rgba(ac, 74)},stop:1 {rgba(ac, 46)});"
            f"color:#ffffff;"
            f"border:1px solid {rgba(ac, 190)};"
            f"border-radius:{R_CTRL}px;"
            f"font-family:'{UI_FONT}';font-size:{T_SMALL}px;font-weight:700;"
            "padding:0 18px;"
            "}"
            "QPushButton:hover{"
            f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {rgba(ac, 96)},stop:1 {rgba(ac, 62)});"
            "}"
            "QPushButton:pressed{"
            f"background:{rgba(ac, 40)};"
            "}"
            "QPushButton:disabled{"
            f"background:{rgba('#01040f', 140)};"
            f"color:{rgba(TEXT_MUTED, 120)};"
            f"border-color:{rgba(ac, 26)};"
            "}"
        )
    return (
        "QPushButton{"
        f"background:{rgba('#01040f', 120)};"
        f"color:{TEXT_MUTED};"
        f"border:1px solid {rgba(ac, 30)};"
        f"border-radius:{R_CTRL}px;"
        f"font-family:'{UI_FONT}';font-size:{T_SMALL}px;font-weight:600;"
        "padding:0 14px;"
        "}"
        "QPushButton:hover{"
        f"color:{TEXT};"
        f"background:{rgba(ac, 22)};"
        f"border-color:{rgba(ac, 80)};"
        "}"
        "QPushButton:pressed{"
        f"background:{rgba(ac, 34)};"
        "}"
        "QPushButton:disabled{"
        f"color:{rgba(TEXT_MUTED, 90)};"
        f"border-color:{rgba(ac, 18)};"
        "background:transparent;"
        "}"
    )


def scrollbar_qss(accent_color=None, width=8):
    ac = accent(accent_color)
    return (
        "QScrollBar:vertical{"
        f"background:{rgba('#01040f', 90)};"
        f"width:{width}px;border:none;margin:4px 2px 4px 2px;border-radius:4px;"
        "}"
        "QScrollBar::handle:vertical{"
        f"background:{rgba(ac, 70)};"
        "border:none;border-radius:4px;min-height:28px;"
        "}"
        "QScrollBar::handle:vertical:hover{"
        f"background:{rgba(ac, 120)};"
        "}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;width:0;}"
        "QScrollBar:horizontal{height:0;width:0;}"
    )


def scroll_area_qss(accent_color=None, width=8):
    return "QScrollArea{background:transparent;border:none;}" + scrollbar_qss(accent_color, width)


def progress_qss(accent_color=None):
    ac = accent(accent_color)
    sec = accent_secondary(ac)
    return (
        "QProgressBar{"
        f"background:{rgba('#01040f', 170)};"
        f"border:1px solid {rgba(ac, 26)};"
        "border-radius:4px;"
        "}"
        "QProgressBar::chunk{"
        f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {ac},stop:1 {sec});"
        "border-radius:3px;"
        "}"
    )


# ---------------------------------------------------------------------------
# Typography helpers
# ---------------------------------------------------------------------------

def label_qss(size=T_BODY, color=TEXT, weight=500, spacing=0.0, mono=False, italic=False):
    family = MONO_FONT if mono else UI_FONT
    return (
        f"color:{color};"
        f"font-family:'{family}';"
        f"font-size:{size}px;"
        f"font-weight:{weight};"
        f"letter-spacing:{spacing}px;"
        f"font-style:{'italic' if italic else 'normal'};"
        "border:none;background:transparent;"
    )


def eyebrow_qss(color):
    return label_qss(T_EYEBROW, color, 800, 1.7, mono=True)


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------

_ICON_CACHE = {}


def _draw_icon(painter, kind, rect, color):
    pen = painter.pen()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cx, cy = x + w / 2, y + h / 2

    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x + w * x1, y + h * y1), QPointF(x + w * x2, y + h * y2))

    def circle(cxr, cyr, r, fill=False):
        rr = QRectF(x + w * (cxr - r), y + h * (cyr - r), w * r * 2, h * r * 2)
        if fill:
            painter.setBrush(QBrush(QColor(color)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rr)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            painter.drawEllipse(rr)

    def rectf(x1, y1, x2, y2, radius=0.12):
        rr = QRectF(x + w * x1, y + h * y1, w * (x2 - x1), h * (y2 - y1))
        painter.drawRoundedRect(rr, w * radius, h * radius)

    if kind == "home":
        painter.drawPolyline(
            QPointF(x + w * 0.16, y + h * 0.48),
            QPointF(cx, y + h * 0.16),
            QPointF(x + w * 0.84, y + h * 0.48),
        )
        rectf(0.26, 0.46, 0.74, 0.86, 0.08)
    elif kind == "gauge":
        painter.drawArc(QRectF(x + w * 0.14, y + h * 0.18, w * 0.72, h * 0.72), 210 * 16, 120 * 16)
        line(0.5, 0.66, 0.66, 0.42)
        circle(0.5, 0.66, 0.075, fill=True)
        line(0.2, 0.78, 0.3, 0.78)
        line(0.7, 0.78, 0.8, 0.78)
    elif kind == "cpu":
        rectf(0.26, 0.26, 0.74, 0.74, 0.12)
        rectf(0.42, 0.42, 0.58, 0.58, 0.1)
        for t in (0.36, 0.5, 0.64):
            line(t, 0.1, t, 0.26)
            line(t, 0.74, t, 0.9)
            line(0.1, t, 0.26, t)
            line(0.74, t, 0.9, t)
    elif kind == "gpu":
        rectf(0.12, 0.3, 0.88, 0.66, 0.12)
        circle(0.34, 0.48, 0.11)
        line(0.56, 0.42, 0.78, 0.42)
        line(0.56, 0.55, 0.78, 0.55)
        line(0.24, 0.66, 0.24, 0.84)
    elif kind == "ram":
        rectf(0.12, 0.32, 0.88, 0.68, 0.1)
        for t in (0.26, 0.42, 0.58, 0.74):
            line(t, 0.44, t, 0.56)
        line(0.2, 0.68, 0.2, 0.82)
        line(0.8, 0.68, 0.8, 0.82)
    elif kind == "input":
        painter.drawRoundedRect(QRectF(x + w * 0.32, y + h * 0.12, w * 0.36, h * 0.76), w * 0.18, h * 0.18)
        line(0.5, 0.12, 0.5, 0.42)
    elif kind == "network":
        circle(0.5, 0.5, 0.36)
        painter.drawEllipse(QRectF(x + w * 0.36, y + h * 0.14, w * 0.28, h * 0.72))
        line(0.16, 0.4, 0.84, 0.4)
        line(0.16, 0.62, 0.84, 0.62)
    elif kind == "power":
        painter.drawArc(QRectF(x + w * 0.2, y + h * 0.22, w * 0.6, h * 0.6), 120 * 16, 300 * 16)
        line(0.5, 0.1, 0.5, 0.44)
    elif kind == "shield":
        path = QPainterPath()
        path.moveTo(x + w * 0.5, y + h * 0.12)
        path.lineTo(x + w * 0.84, y + h * 0.28)
        path.lineTo(x + w * 0.84, y + h * 0.52)
        path.quadTo(x + w * 0.84, y + h * 0.78, x + w * 0.5, y + h * 0.9)
        path.quadTo(x + w * 0.16, y + h * 0.78, x + w * 0.16, y + h * 0.52)
        path.lineTo(x + w * 0.16, y + h * 0.28)
        path.closeSubpath()
        painter.drawPath(path)
        line(0.38, 0.5, 0.47, 0.6)
        line(0.47, 0.6, 0.64, 0.4)
    elif kind == "trash":
        line(0.2, 0.3, 0.8, 0.3)
        rectf(0.28, 0.3, 0.72, 0.86, 0.1)
        line(0.4, 0.16, 0.6, 0.16)
        line(0.42, 0.44, 0.42, 0.72)
        line(0.58, 0.44, 0.58, 0.72)
    elif kind == "sparkle":
        path = QPainterPath()
        path.moveTo(cx, y + h * 0.12)
        path.lineTo(x + w * 0.6, cy)
        path.lineTo(cx, y + h * 0.88)
        path.lineTo(x + w * 0.4, cy)
        path.closeSubpath()
        painter.drawPath(path)
        line(0.78, 0.2, 0.78, 0.36)
        line(0.7, 0.28, 0.86, 0.28)
    elif kind == "eye":
        path = QPainterPath()
        path.moveTo(x + w * 0.12, cy)
        path.quadTo(cx, y + h * 0.16, x + w * 0.88, cy)
        path.quadTo(cx, y + h * 0.84, x + w * 0.12, cy)
        path.closeSubpath()
        painter.drawPath(path)
        circle(0.5, 0.5, 0.12, fill=True)
    elif kind == "sliders":
        for i, (yy, kx) in enumerate([(0.28, 0.34), (0.5, 0.66), (0.72, 0.44)]):
            line(0.14, yy, 0.86, yy)
            circle(kx, yy, 0.09, fill=True)
    elif kind == "box":
        rectf(0.18, 0.28, 0.82, 0.8, 0.1)
        line(0.18, 0.44, 0.82, 0.44)
        line(0.42, 0.28, 0.42, 0.44)
    elif kind == "gamepad":
        painter.drawRoundedRect(QRectF(x + w * 0.1, y + h * 0.3, w * 0.8, h * 0.44), w * 0.2, h * 0.2)
        line(0.3, 0.44, 0.3, 0.6)
        line(0.22, 0.52, 0.38, 0.52)
        circle(0.66, 0.46, 0.05, fill=True)
        circle(0.76, 0.58, 0.05, fill=True)
    elif kind == "layers":
        painter.drawPolyline(
            QPointF(cx, y + h * 0.14),
            QPointF(x + w * 0.84, y + h * 0.36),
            QPointF(cx, y + h * 0.58),
            QPointF(x + w * 0.16, y + h * 0.36),
            QPointF(cx, y + h * 0.14),
        )
        painter.drawPolyline(
            QPointF(x + w * 0.16, y + h * 0.6),
            QPointF(cx, y + h * 0.82),
            QPointF(x + w * 0.84, y + h * 0.6),
        )
    elif kind == "bolt":
        path = QPainterPath()
        path.moveTo(x + w * 0.56, y + h * 0.12)
        path.lineTo(x + w * 0.3, y + h * 0.54)
        path.lineTo(x + w * 0.5, y + h * 0.54)
        path.lineTo(x + w * 0.44, y + h * 0.88)
        path.lineTo(x + w * 0.7, y + h * 0.46)
        path.lineTo(x + w * 0.5, y + h * 0.46)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "restore":
        painter.drawArc(QRectF(x + w * 0.18, y + h * 0.2, w * 0.64, h * 0.64), 30 * 16, 280 * 16)
        painter.drawPolyline(
            QPointF(x + w * 0.18, y + h * 0.16),
            QPointF(x + w * 0.18, y + h * 0.42),
            QPointF(x + w * 0.44, y + h * 0.42),
        )
    elif kind == "gear":
        circle(0.5, 0.5, 0.16)
        painter.drawEllipse(QRectF(x + w * 0.26, y + h * 0.26, w * 0.48, h * 0.48))
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1 = cx + math.cos(rad) * w * 0.24
            y1 = cy + math.sin(rad) * h * 0.24
            x2 = cx + math.cos(rad) * w * 0.38
            y2 = cy + math.sin(rad) * h * 0.38
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif kind == "user":
        circle(0.5, 0.34, 0.17)
        painter.drawArc(QRectF(x + w * 0.22, y + h * 0.56, w * 0.56, h * 0.56), 0, 2880)
    elif kind == "pulse":
        painter.drawPolyline(
            QPointF(x + w * 0.1, cy),
            QPointF(x + w * 0.32, cy),
            QPointF(x + w * 0.44, y + h * 0.24),
            QPointF(x + w * 0.58, y + h * 0.76),
            QPointF(x + w * 0.7, cy),
            QPointF(x + w * 0.9, cy),
        )
    elif kind == "search":
        circle(0.42, 0.42, 0.26)
        line(0.62, 0.62, 0.86, 0.86)
    elif kind == "check":
        line(0.22, 0.52, 0.42, 0.72)
        line(0.42, 0.72, 0.8, 0.3)
    elif kind == "close":
        line(0.26, 0.26, 0.74, 0.74)
        line(0.74, 0.26, 0.26, 0.74)
    elif kind == "chevron":
        painter.drawPolyline(
            QPointF(x + w * 0.34, y + h * 0.28),
            QPointF(x + w * 0.66, cy),
            QPointF(x + w * 0.34, y + h * 0.72),
        )
    elif kind == "download":
        line(0.5, 0.16, 0.5, 0.62)
        painter.drawPolyline(
            QPointF(x + w * 0.32, y + h * 0.46),
            QPointF(cx, y + h * 0.64),
            QPointF(x + w * 0.68, y + h * 0.46),
        )
        line(0.22, 0.82, 0.78, 0.82)
    elif kind == "refresh":
        painter.drawArc(QRectF(x + w * 0.2, y + h * 0.2, w * 0.6, h * 0.6), 60 * 16, 250 * 16)
        painter.drawPolyline(
            QPointF(x + w * 0.78, y + h * 0.16),
            QPointF(x + w * 0.8, y + h * 0.42),
            QPointF(x + w * 0.54, y + h * 0.4),
        )
    else:  # dot fallback
        circle(0.5, 0.5, 0.2, fill=True)


def icon(kind, color, size=18, weight=1.0):
    key = (kind, str(color).lower(), size, weight)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    scale = 2
    pix = QPixmap(size * scale, size * scale)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(
        pix.width() * 0.1,
        pix.height() * 0.1,
        pix.width() * 0.8,
        pix.height() * 0.8,
    )
    pen_width = max(1.3, pix.width() * 0.075) * weight
    pen = QPen(QColor(color), pen_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    _draw_icon(p, kind, rect, color)
    p.end()
    ic = QIcon(pix)
    _ICON_CACHE[key] = ic
    return ic


def paint_icon(painter, kind, rect, color, weight=1.0):
    pen = QPen(QColor(color), max(1.2, rect.width() * 0.085) * weight)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _draw_icon(painter, kind, rect, color)


# ---------------------------------------------------------------------------
# Shared widgets
# ---------------------------------------------------------------------------

class Card(QFrame):
    """Rounded translucent surface with an optional accent glow."""

    def __init__(self, accent_color=None, radius=R_CARD, glow=False, parent=None):
        super().__init__(parent)
        self._accent = accent(accent_color)
        self._radius = radius
        self._glow = glow
        self._effect = None
        self.set_accent(self._accent)

    def set_accent(self, color):
        self._accent = accent(color)
        self.setStyleSheet(card_qss(self._accent, self._radius))
        if self._glow:
            if self._effect is None:
                self._effect = QGraphicsDropShadowEffect(self)
                self._effect.setBlurRadius(46)
                self._effect.setOffset(0, 6)
                self.setGraphicsEffect(self._effect)
            c = QColor(self._accent)
            self._effect.setColor(QColor(c.red(), c.green(), c.blue(), 26))


class Pill(QLabel):
    """Small rounded status/badge chip."""

    def __init__(self, text="", tone="muted", accent_color=None, parent=None):
        super().__init__(parent)
        self._tone = tone
        self._accent = accent(accent_color)
        self.setText(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_accent(self._accent, tone)

    def set_accent(self, color, tone=None):
        self._accent = accent(color)
        if tone is not None:
            self._tone = tone
        col = tone_color(self._tone, self._accent)
        self.setStyleSheet(
            "QLabel{"
            f"background:{rgba(col, 26)};"
            f"color:{col};"
            f"border:1px solid {rgba(col, 70)};"
            f"border-radius:{R_PILL}px;"
            f"font-family:'{MONO_FONT}';font-size:{T_EYEBROW}px;font-weight:700;letter-spacing:0.8px;"
            "padding:3px 9px;"
            "}"
        )

    def set_value(self, text, tone=None, accent_color=None):
        if accent_color is not None:
            self._accent = accent(accent_color)
        self.set_accent(self._accent, tone)
        self.setText(text)


class StatTile(Card):
    """Compact metric tile: eyebrow label, big value, supporting line."""

    _tone = "accent"
    _icon_kind = None

    def __init__(self, label, value, sub="", tone="accent", accent_color=None, icon_kind=None, parent=None):
        super().__init__(accent_color, R_CARD, glow=False, parent=parent)
        self._tone = tone
        self._icon_kind = icon_kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        head.addWidget(self._icon)
        self._label = QLabel(label)
        self._label.setStyleSheet(eyebrow_qss(TEXT_DIM))
        head.addWidget(self._label)
        head.addStretch(1)
        lay.addLayout(head)

        self._value = QLabel(value)
        self._value.setStyleSheet(label_qss(24, TEXT, 800))
        lay.addWidget(self._value)

        self._sub = QLabel(sub)
        self._sub.setStyleSheet(label_qss(T_SMALL, TEXT_MUTED, 500))
        self._sub.setWordWrap(True)
        lay.addWidget(self._sub)
        lay.addStretch(1)
        self.setMinimumHeight(104)
        self.refresh()

    def refresh(self):
        if not hasattr(self, "_value"):
            return
        col = tone_color(self._tone, self._accent)
        if self._icon_kind:
            self._icon.setPixmap(icon(self._icon_kind, col, 16).pixmap(16, 16))
        self._value.setStyleSheet(label_qss(24, TEXT, 800))
        self._sub.setStyleSheet(label_qss(T_SMALL, TEXT_MUTED, 500))

    def set_accent(self, color):
        super().set_accent(color)
        self.refresh()

    def set_value(self, value, sub=None, tone=None):
        if tone is not None:
            self._tone = tone
        self._value.setText(str(value))
        if sub is not None:
            self._sub.setText(str(sub))
        self.refresh()


class PageHeader(QWidget):
    """Consistent page heading: eyebrow, title, subtitle, badge + actions."""

    def __init__(self, eyebrow, title, subtitle="", accent_color=None, parent=None):
        super().__init__(parent)
        self._accent = accent(accent_color)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        self._eyebrow = QLabel(eyebrow)
        self._eyebrow.setStyleSheet(eyebrow_qss(self._accent))
        self._title = QLabel(title)
        self._title.setStyleSheet(label_qss(T_DISPLAY, TEXT, 700))
        col.addWidget(self._eyebrow)
        col.addWidget(self._title)
        top.addLayout(col, 1)

        self._badge = Pill("", "accent", self._accent)
        self._badge.hide()
        top.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        top.addLayout(self._actions)
        root.addLayout(top)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(label_qss(T_BODY, TEXT_MUTED, 500))
        self._subtitle.setVisible(bool(subtitle))
        root.addWidget(self._subtitle)

    def set_subtitle(self, text):
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_badge(self, text, tone="accent"):
        if not text:
            self._badge.hide()
            return
        self._badge.set_value(text, tone, self._accent)
        self._badge.show()

    def add_action(self, widget):
        self._actions.addWidget(widget)

    def set_accent(self, color):
        self._accent = accent(color)
        self._eyebrow.setStyleSheet(eyebrow_qss(self._accent))
        if self._badge.isVisible():
            self._badge.set_accent(self._accent)


class EmptyState(QWidget):
    """Friendly placeholder for lists with no content yet."""

    def __init__(self, icon_kind, title, hint="", accent_color=None, parent=None):
        super().__init__(parent)
        self._accent = accent(accent_color)
        self._icon_kind = icon_kind
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 36, 24, 36)
        root.setSpacing(10)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFixedSize(52, 52)
        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet(label_qss(T_TITLE, TEXT, 700))
        self._hint = QLabel(hint)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setMaximumWidth(430)
        self._hint.setStyleSheet(label_qss(T_BODY, TEXT_MUTED, 500))
        root.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self._title)
        root.addWidget(self._hint, 0, Qt.AlignmentFlag.AlignHCenter)
        self.refresh()

    def refresh(self):
        self._icon.setPixmap(icon(self._icon_kind, rgba(self._accent, 190), 40).pixmap(40, 40))

    def set_accent(self, color):
        self._accent = accent(color)
        self.refresh()


class SectionCaption(QLabel):
    def __init__(self, text, accent_color=None, parent=None):
        super().__init__(text, parent)
        self._accent = accent(accent_color)
        self.set_accent(self._accent)

    def set_accent(self, color):
        self._accent = accent(color)
        self.setStyleSheet(eyebrow_qss(mix(self._accent, TEXT_MUTED, 0.45)))


def make_button(text, primary=False, accent_color=None, icon_kind=None, danger=False, height=36):
    btn = QPushButton(text)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(button_qss(primary, accent_color, danger))
    if icon_kind:
        btn.setIcon(icon(icon_kind, "#ffffff" if primary else TEXT_MUTED, 14))
        btn.setIconSize(QSize(14, 14))
    return btn


def paint_backdrop(painter, rect, width, height, offset_x=0, offset_y=0, accent_color=None):
    """Window backdrop: deep navy, two soft accent glows, faint grid, vignette."""
    ac = QColor(accent(accent_color))
    sec = QColor(accent_secondary(ac.name()))
    painter.fillRect(rect, QColor(BG))

    top_glow = QRadialGradient(QPointF(width * 0.42 - offset_x, -height * 0.18 - offset_y), max(1, width * 0.62))
    top_glow.setColorAt(0.0, QColor(ac.red(), ac.green(), ac.blue(), 52))
    top_glow.setColorAt(0.4, QColor(ac.red(), ac.green(), ac.blue(), 16))
    top_glow.setColorAt(1.0, QColor(ac.red(), ac.green(), ac.blue(), 0))
    painter.fillRect(rect, QBrush(top_glow))

    corner_glow = QRadialGradient(QPointF(width * 0.98 - offset_x, height * 1.02 - offset_y), max(1, width * 0.4))
    corner_glow.setColorAt(0.0, QColor(sec.red(), sec.green(), sec.blue(), 40))
    corner_glow.setColorAt(0.35, QColor(sec.red(), sec.green(), sec.blue(), 14))
    corner_glow.setColorAt(1.0, QColor(sec.red(), sec.green(), sec.blue(), 0))
    painter.fillRect(rect, QBrush(corner_glow))

    painter.setPen(QPen(QColor(ac.red(), ac.green(), ac.blue(), 16), 1))
    step = 52
    local_w = rect.width()
    local_h = rect.height()
    for x in range(0, width + step, step):
        lx = x - offset_x
        painter.drawLine(lx, 0, lx, local_h)
    for y in range(0, height + step, step):
        ly = y - offset_y
        painter.drawLine(0, ly, local_w, ly)
