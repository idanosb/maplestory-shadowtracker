"""Windows.Media.Ocr wrapper (en-US, offline, built into Windows 10)."""
import asyncio
import threading

import cv2
import numpy as np
from winsdk.windows.globalization import Language
from winsdk.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.storage.streams import DataWriter


def _to_software_bitmap(img: np.ndarray) -> SoftwareBitmap:
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    bgra = cv2.cvtColor(big, cv2.COLOR_BGR2BGRA)
    h, w = bgra.shape[:2]
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    return SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h
    )


async def _recognize(bitmap: SoftwareBitmap, engine: OcrEngine):
    return await engine.recognize_async(bitmap)


_local = threading.local()


def _engine() -> OcrEngine:
    """One en-US engine per thread (created on first use)."""
    engine = getattr(_local, "engine", None)
    if engine is None:
        engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            raise RuntimeError("en-US OCR not available")
        _local.engine = engine
    return engine


def ocr_lines(img: np.ndarray) -> list[str]:
    """OCR a BGR image; return text lines, top to bottom."""
    engine = _engine()
    result = asyncio.run(_recognize(_to_software_bitmap(img), engine))
    return [line.text for line in result.lines]
