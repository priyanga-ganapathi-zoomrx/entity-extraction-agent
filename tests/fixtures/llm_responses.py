"""Mock LLM responses for testing."""
from src.agents.drug.schemas import (
    ExtractionResult,
    ValidationResult,
    ChecksPerformed,
    CheckResult,
    IssueFound,
    SearchResult,
)


# =============================================================================
# Drug Extraction Mock Responses
# =============================================================================

MOCK_DRUG_EXTRACTION_SINGLE = ExtractionResult(
    reasoning=[
        "The abstract title mentions 'Pembrolizumab in Advanced NSCLC'",
        "Pembrolizumab is the primary drug being studied",
        "Chemotherapy is mentioned as a comparator",
    ],
    primary_drugs=["Pembrolizumab"],
    secondary_drugs=[],
    comparator_drugs=["Chemotherapy"],
)

MOCK_DRUG_EXTRACTION_EMPTY = ExtractionResult(
    reasoning=["No drugs found in the abstract"],
    primary_drugs=[],
    secondary_drugs=[],
    comparator_drugs=[],
)

MOCK_DRUG_EXTRACTION_COMBINATION = ExtractionResult(
    reasoning=[
        "The abstract studies Nivolumab plus Ipilimumab combination",
        "Both drugs are primary investigational drugs",
        "Sunitinib is used as a comparator",
    ],
    primary_drugs=["Nivolumab", "Ipilimumab"],
    secondary_drugs=[],
    comparator_drugs=["Sunitinib"],
)

MOCK_DRUG_EXTRACTION_WITH_SECONDARY = ExtractionResult(
    reasoning=[
        "Pembrolizumab is the primary drug",
        "Carboplatin is used in combination (secondary)",
    ],
    primary_drugs=["Pembrolizumab"],
    secondary_drugs=["Carboplatin"],
    comparator_drugs=[],
)


# =============================================================================
# Drug Validation Mock Responses
# =============================================================================

MOCK_DRUG_VALIDATION_PASS = ValidationResult(
    validation_status="PASS",
    validation_confidence=0.95,
    missed_drugs=[],
    grounded_search_performed=False,
    search_results=[],
    issues_found=[],
    checks_performed=ChecksPerformed(
        hallucination_detection=CheckResult(
            passed=True,
            note="No hallucinated drugs found"
        ),
        omission_detection=CheckResult(
            passed=True,
            note="No drugs omitted from extraction"
        ),
        rule_compliance=CheckResult(
            passed=True,
            note="All categorization rules followed"
        ),
        misclassification_detection=CheckResult(
            passed=True,
            note="All drugs correctly classified"
        ),
        synonym_association_detection=CheckResult(
            passed=True,
            note="No synonym issues found"
        ),
    ),
    validation_reasoning="All extraction results are accurate and complete.",
)

MOCK_DRUG_VALIDATION_REVIEW = ValidationResult(
    validation_status="REVIEW",
    validation_confidence=0.70,
    missed_drugs=["Durvalumab"],
    grounded_search_performed=True,
    search_results=[
        SearchResult(
            drug_queried="Durvalumab",
            is_therapeutic_drug=True,
            source_url="https://www.drugs.com/durvalumab.html",
            source_title="Durvalumab Information",
            evidence="Durvalumab is a PD-L1 blocking antibody",
            confidence="high",
        )
    ],
    issues_found=[
        IssueFound(
            check_type="omission",
            severity="medium",
            description="Durvalumab was mentioned in the abstract but not extracted",
            evidence="The abstract mentions 'compared with Durvalumab' in the methods section",
            drug="Durvalumab",
        )
    ],
    checks_performed=ChecksPerformed(
        hallucination_detection=CheckResult(
            passed=True,
            note="No hallucinated drugs found"
        ),
        omission_detection=CheckResult(
            passed=False,
            note="One drug was omitted from extraction"
        ),
        rule_compliance=CheckResult(
            passed=True,
            note="All categorization rules followed"
        ),
        misclassification_detection=CheckResult(
            passed=True,
            note="All drugs correctly classified"
        ),
        synonym_association_detection=CheckResult(
            passed=True,
            note="No synonym issues found"
        ),
    ),
    validation_reasoning="Found one potential omission that requires review.",
)

MOCK_DRUG_VALIDATION_FAIL = ValidationResult(
    validation_status="FAIL",
    validation_confidence=0.50,
    missed_drugs=["Nivolumab"],
    grounded_search_performed=True,
    search_results=[
        SearchResult(
            drug_queried="Aspirin",
            is_therapeutic_drug=False,
            source_url="https://www.drugs.com/aspirin.html",
            source_title="Aspirin Information",
            evidence="Aspirin is an over-the-counter pain reliever",
            confidence="high",
        )
    ],
    issues_found=[
        IssueFound(
            check_type="hallucination",
            severity="high",
            description="Aspirin is not a therapeutic drug for cancer",
            evidence="Aspirin was extracted but is only mentioned as supportive care",
            drug="Aspirin",
            rule_reference="Only extract therapeutic drugs, not supportive care",
        ),
        IssueFound(
            check_type="omission",
            severity="high",
            description="Nivolumab was not extracted despite being the primary drug",
            evidence="Abstract title: 'Study of Nivolumab in melanoma'",
            drug="Nivolumab",
        ),
    ],
    checks_performed=ChecksPerformed(
        hallucination_detection=CheckResult(
            passed=False,
            note="Hallucinated drug found (Aspirin)"
        ),
        omission_detection=CheckResult(
            passed=False,
            note="Primary drug omitted (Nivolumab)"
        ),
        rule_compliance=CheckResult(
            passed=False,
            note="Extracted non-therapeutic drug"
        ),
        misclassification_detection=CheckResult(
            passed=True,
            note="Classification correct for extracted drugs"
        ),
        synonym_association_detection=CheckResult(
            passed=True,
            note="No synonym issues"
        ),
    ),
    validation_reasoning="Multiple critical issues found requiring extraction redo.",
)

MOCK_DRUG_VALIDATION_WITH_MISCLASSIFICATION = ValidationResult(
    validation_status="REVIEW",
    validation_confidence=0.75,
    missed_drugs=[],
    grounded_search_performed=False,
    search_results=[],
    issues_found=[
        IssueFound(
            check_type="misclassification",
            severity="medium",
            description="Carboplatin should be secondary, not comparator",
            evidence="Abstract states 'Pembrolizumab plus Carboplatin combination'",
            drug="Carboplatin",
            correct_category="secondary_drugs",
        )
    ],
    checks_performed=ChecksPerformed(
        hallucination_detection=CheckResult(
            passed=True,
            note="No hallucinated drugs"
        ),
        omission_detection=CheckResult(
            passed=True,
            note="No omissions"
        ),
        rule_compliance=CheckResult(
            passed=True,
            note="Rules followed"
        ),
        misclassification_detection=CheckResult(
            passed=False,
            note="One drug misclassified"
        ),
        synonym_association_detection=CheckResult(
            passed=True,
            note="No synonym issues"
        ),
    ),
    validation_reasoning="Drug categorization needs correction.",
)
