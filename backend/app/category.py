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
    "Groceries": ["grocery", "supermarket", "bigbasket", "blinkit", "dmart", "walmart", "grocer"],
    "Food and dining": ["restaurant", "cafe", "swiggy", "zomato", "uber eats", "food", "pizza", "coffee"],
    "Shopping": ["amazon", "flipkart", "myntra", "shopping", "store", "mall"],
    "Transport and travel": ["uber", "ola", "taxi", "metro", "rail", "air", "fuel", "petrol", "gas station"],
    "Rent and housing": ["rent", "lease", "mortgage", "hoa"],
    "Utilities and bills": ["electric", "water", "internet", "wifi", "bill", "mobile", "recharge"],
    "Healthcare": ["pharmacy", "hospital", "clinic", "medical", "doctor"],
    "Education": ["school", "college", "university", "course", "tuition"],
    "Subscriptions": ["netflix", "spotify", "subscription", "prime"],
    "Transfers": ["transfer", "neft", "imps", "rtgs", "upi transfer"],
}


def categorize_transaction(description: str = "", vendor: str = "", upi_ref: str = "") -> tuple[str, str]:
    text = " ".join([str(description), str(vendor), str(upi_ref)]).lower()
    for cat, words in KEYWORDS.items():
        for w in words:
            if w in text:
                return cat, "rule"
    return "Uncategorized", "uncertain"
