from __future__ import annotations

import re


CATEGORIES = [
    "Groceries",
    "Food and dining",
    "Shopping",
    "Transport and travel",
    "Rent and housing",
    "Utilities and bills",
    "Healthcare",
    "Education",
    "Subscriptions",
    "Transfers",
    "Other",
    "Uncategorized",
]


KEYWORDS = {
    "Groceries": ["grocery", "supermarket", "bigbasket", "blinkit", "dmart", "walmart", "grocer", "kirana"],
    "Food and dining": ["restaurant", "cafe", "swiggy", "zomato", "uber eats", "food", "pizza", "coffee", "bakery", "hotel"],
    "Shopping": ["amazon", "flipkart", "myntra", "shopping", "store", "mall", "mart"],
    "Transport and travel": ["uber", "ola", "taxi", "metro", "rail", "air", "fuel", "petrol", "gas station", "bus", "travel"],
    "Rent and housing": ["rent", "lease", "mortgage", "hoa", "maintenance"],
    "Utilities and bills": ["electric", "water", "internet", "wifi", "bill", "mobile", "recharge", "broadband", "gas bill"],
    "Healthcare": ["pharmacy", "hospital", "clinic", "medical", "doctor", "medic", "diagnostic"],
    "Education": ["school", "college", "university", "course", "tuition", "exam", "coaching"],
    "Subscriptions": ["netflix", "spotify", "subscription", "prime", "youtube premium"],
    "Transfers": ["transfer", "neft", "imps", "rtgs", "upi transfer", "self transfer"],
}


def categorize_transaction(description: str = "", vendor: str = "", upi_ref: str = "") -> tuple[str, str]:
    text = " ".join([str(description), str(vendor), str(upi_ref)]).lower()
    for cat, words in KEYWORDS.items():
        for w in words:
            if w in text:
                return cat, "rule"
    return "Uncategorized", "uncertain"
