import pytest

from scanamzn.config import DEFAULT_EXCLUDE, DEFAULT_REQUIRE_ANY
from scanamzn.filters import TitleFilter, face_value

default = TitleFilter(DEFAULT_REQUIRE_ANY, DEFAULT_EXCLUDE)


@pytest.mark.parametrize(
    "title",
    [
        "Sony PlayStation Network Wallet Top-Up ₹ 1000 (Digital Code - Email Delivery)",
        "PlayStation Store Gift Card Rs. 3000",
        "PSN Wallet Topup INR 2000",
        "PlayStation Gift Cards - ₹4000 (Email Delivery)",
        "Sony PSN Card 500 INR",
    ],
)
def test_matches_indian_playstation_gift_cards(title):
    assert default.matches(title), default.why_not(title)


@pytest.mark.parametrize(
    "title",
    [
        "PlayStation Store $10 Gift Card - US Account [Digital Code]",
        "PlayStation Store Gift Card 50 AED UAE",
        "Sony DualSense Wireless Controller for PS5 (White)",
        "PlayStation Plus Essential 3 Month Membership (Digital Code)",
        "Xbox Gift Card ₹1000",
        "PS5 Console Skin Sticker",
        "Marvel's Spider-Man 2 PS5 Digital Code",
    ],
)
def test_rejects_other_items(title):
    assert not default.matches(title)


def test_whole_word_matching():
    f = TitleFilter([["card"]], ["us"])
    assert f.matches("USB card reader")  # "us" must not match inside "USB"
    assert not f.matches("Gift card for US store")


def test_why_not_reports_reason():
    assert default.why_not("Xbox Gift Card") == "excluded term 'xbox'"
    assert default.why_not("PlayStation 5 Slim") == "missing required term"


@pytest.mark.parametrize(
    "title, value",
    [
        ("Sony PlayStation Network Wallet Top-Up ₹ 1000 (Digital Code)", 1000),
        ("PlayStation Store Gift Card Rs. 3,000", 3000),
        ("PSN Wallet Rs.500", 500),
        ("PSN Wallet Topup INR 2000", 2000),
        ("Sony PSN Card 4000 INR", 4000),
        ("PlayStation Gift Card 5000 Rupees", 5000),
        ("PlayStation Gift Card", None),
        ("PS5 3 Month Membership", None),
    ],
)
def test_face_value(title, value):
    assert face_value(title) == value
