"""Small, local TF-IDF retrieval layer; no embedding API or model download required."""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

POLICIES = [
    {"source": "POLICY-DUP-01", "text": "Duplicate payment review: compare vendor, transaction date, amount, invoice reference, and description. Verify the original invoice and payment ledger before blocking or reversing a payment."},
    {"source": "POLICY-APP-02", "text": "High-value transaction review: compare transaction amount with the organization's approval threshold, purchase order, invoice, budget owner approval, and proof of delivery."},
    {"source": "POLICY-DATA-03", "text": "Data quality review: investigate missing transaction IDs, invalid dates, negative values, inconsistent currency, and missing invoice references. Preserve the original source file and document corrections."},
    {"source": "POLICY-REF-04", "text": "Invoice reconciliation: match invoice and payment records using invoice number, vendor, currency, and amount. Treat partial payments, taxes, discounts, and timing differences as possible explanations that require evidence."},
    {"source": "POLICY-HUMAN-05", "text": "Audit findings are indicators for investigation, not proof of fraud. An authorized human reviewer must validate source documents and approve financial actions."},
]

def retrieve_context(question: str, evidence: str = "", top_k: int = 3) -> list[dict]:
    docs = POLICIES + ([{"source": "TRANSACTION-EVIDENCE", "text": evidence}] if evidence else [])
    corpus = [d["text"] for d in docs]
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(corpus + [question])
    scores = cosine_similarity(matrix[-1], matrix[:-1]).flatten()
    ranked = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
    return [{**docs[i], "score": float(scores[i])} for i in ranked[:top_k] if scores[i] > 0]
