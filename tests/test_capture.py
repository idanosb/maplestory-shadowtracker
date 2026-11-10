import cv2
import numpy as np

from expstrip.capture import is_blank


def test_is_blank_black():
    assert is_blank(np.zeros((100, 100, 3), np.uint8))


def test_is_blank_game():
    img = cv2.imread("docs/reference/game-screenshot-kerning.webp")
    assert img is not None
    assert not is_blank(img)
