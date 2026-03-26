"""Sample abstracts for testing."""

SAMPLE_DRUG_ABSTRACT = {
    "abstract_id": 123,
    "abstract_title": "Efficacy of Pembrolizumab in Advanced NSCLC",
    "session_title": "Lung Cancer",
    "full_abstract": """
Background: Non-small cell lung cancer (NSCLC) remains a leading cause of cancer mortality.
Methods: This phase 3 trial evaluated Pembrolizumab vs chemotherapy in 500 patients.
Results: Pembrolizumab showed superior overall survival (OS: 18.2 vs 12.1 months).
Conclusion: Pembrolizumab is effective in advanced NSCLC.
    """.strip(),
    "congress_id": 1,
    "batch_id": 100,
}

SAMPLE_DRUG_CLASS_ABSTRACT = {
    "abstract_id": 456,
    "abstract_title": "Combination of Pembrolizumab + Carboplatin in NSCLC",
    "session_title": "Combination Therapy",
    "full_abstract": """
Background: Combination therapy may improve outcomes.
Methods: Pembrolizumab + Carboplatin was tested in 300 patients.
Results: The combination showed ORR of 65%.
    """.strip(),
    "congress_id": 1,
    "batch_id": 100,
}

SAMPLE_INDICATION_ABSTRACT = {
    "abstract_id": 789,
    "abstract_title": "Pembrolizumab in Metastatic Melanoma",
    "session_title": "Melanoma",
    "full_abstract": """
Background: Melanoma treatment has evolved with immunotherapy.
Methods: 200 patients with metastatic melanoma received Pembrolizumab.
Results: Response rate was 40% with durable responses.
    """.strip(),
    "congress_id": 1,
    "batch_id": 100,
}

EMPTY_ABSTRACT = {
    "abstract_id": 999,
    "abstract_title": "",
    "session_title": "",
    "full_abstract": "",
    "congress_id": 1,
    "batch_id": 100,
}

MULTI_DRUG_ABSTRACT = {
    "abstract_id": 555,
    "abstract_title": "Nivolumab Plus Ipilimumab Versus Sunitinib in Advanced Renal Cell Carcinoma",
    "session_title": "Kidney Cancer",
    "full_abstract": """
Background: First-line treatment options for advanced renal cell carcinoma include targeted therapies and immunotherapy.
Methods: We compared the combination of Nivolumab plus Ipilimumab with Sunitinib in previously untreated patients with advanced clear-cell renal-cell carcinoma.
Results: The Nivolumab-plus-Ipilimumab group had longer overall survival (median not reached vs 26 months) and higher objective response rates (42% vs 27%) than the Sunitinib group.
Conclusion: Nivolumab plus Ipilimumab resulted in significantly longer overall survival and a higher objective response rate than Sunitinib.
    """.strip(),
    "congress_id": 1,
    "batch_id": 100,
}
