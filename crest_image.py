"""Genera una imagen con los escudos de dos equipos y sus nombres (para Telegram)."""
import io
import logging
import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CREST_SIZE = 80
PADDING = 24
IMG_WIDTH = 560
IMG_HEIGHT = 220
FONT_SIZE = 15
SCORE_FONT_SIZE = 32
# Zonas para evitar solapamiento: izquierda (local), centro (marcador), derecha (visitante)
ZONE_LEFT_END = 240
ZONE_RIGHT_START = 320
TIMEOUT = 8


def _download_image(url: str) -> Image.Image | None:
    """Descarga una imagen desde URL y la devuelve como PIL Image."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        return img.convert("RGBA")
    except Exception as e:
        logger.debug("No se pudo descargar escudo %s: %s", url[:50], e)
        return None


def _get_font(size: int = FONT_SIZE):
    """Fuente disponible en Windows/Linux."""
    import os
    for path in (
        "arial.ttf",
        "Arial.ttf",
        os.path.join(os.environ.get("WINDIR", ""), "Fonts", "arial.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_match_image(
    crest_home_url: str | None,
    crest_away_url: str | None,
    name_home: str,
    name_away: str,
    score_str: str = "vs",
) -> io.BytesIO:
    """
    Crea una imagen con escudos y nombres de ambos equipos y el marcador (o 'vs').
    Tarjeta más grande con zonas separadas: nombres a los lados, marcador abajo centrado.
    Devuelve BytesIO con PNG.
    """
    img = Image.new("RGB", (IMG_WIDTH, IMG_HEIGHT), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = _get_font(FONT_SIZE)
    font_score = _get_font(SCORE_FONT_SIZE)

    # --- Fila superior: escudos y nombres (sin marcador en medio) ---
    row1_y = 28
    crest_y = 20

    def place_crest(url: str | None, x: int) -> None:
        if not url:
            return
        pil = _download_image(url)
        if pil:
            pil = pil.resize((CREST_SIZE, CREST_SIZE), Image.Resampling.LANCZOS)
            if pil.mode == "RGBA":
                img.paste(pil, (x, crest_y), pil)
            else:
                img.paste(pil, (x, crest_y))

    # Zona local: escudo + nombre (nombre no pasa de ZONE_LEFT_END)
    place_crest(crest_home_url, PADDING)
    max_chars_home = 16  # cabe entre escudo y centro
    name_h = (name_home[:max_chars_home] + "…") if len(name_home) > max_chars_home else name_home
    name_h_x = PADDING + CREST_SIZE + 12
    draw.text((name_h_x, row1_y + 8), name_h, fill=(0, 0, 0), font=font)

    # Zona visitante: nombre + escudo (nombre no invade el centro)
    x_away_crest = IMG_WIDTH - PADDING - CREST_SIZE
    place_crest(crest_away_url, x_away_crest)
    max_chars_away = 14
    name_a = (name_away[:max_chars_away] + "…") if len(name_away) > max_chars_away else name_away
    bbox_a = draw.textbbox((0, 0), name_a, font=font)
    name_a_w = bbox_a[2] - bbox_a[0]
    name_a_x = x_away_crest - name_a_w - 12
    if name_a_x < ZONE_RIGHT_START:
        name_a = (name_away[:10] + "…") if len(name_away) > 10 else name_away
        name_a_w = draw.textbbox((0, 0), name_a, font=font)[2] - draw.textbbox((0, 0), name_a, font=font)[0]
        name_a_x = x_away_crest - name_a_w - 12
    draw.text((name_a_x, row1_y + 8), name_a, fill=(0, 0, 0), font=font)

    # --- Fila inferior: solo el marcador, centrado y grande ---
    score_y = 130
    bbox = draw.textbbox((0, 0), score_str, font=font_score)
    tw = bbox[2] - bbox[0]
    center_x = (IMG_WIDTH - tw) // 2
    draw.text((center_x, score_y), score_str, fill=(40, 40, 40), font=font_score)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
