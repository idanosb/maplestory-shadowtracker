import cv2

from expstrip.vision.ocr import ocr_lines


def test_ocr_reads_collapsed_title():
    img = cv2.imread("docs/reference/game-screenshot-kerning.webp")[0:30, 0:330]
    text = " ".join(ocr_lines(img))
    assert "Kerning City Pharmacy" in text


def test_ocr_reads_expanded_title():
    img = cv2.imread("docs/reference/game-screenshot-minimap-expanded.webp")[35:90, 65:320]
    lines = ocr_lines(img)
    assert any("Victoria Road" in l for l in lines) and any("Kerning City" in l for l in lines)
