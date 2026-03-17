"""Mock responses for indication testing."""
from src.agents.indication.schemas import (
    ExtractionLLMResponse,
    RuleRetrieved,
    ComponentIdentified,
)


# =============================================================================
# Indication Extraction Mock Responses
# =============================================================================

MOCK_INDICATION_EXTRACTION_SUCCESS = ExtractionLLMResponse(
    reasoning="Found 'NSCLC' in title, which is a disease abbreviation for Non-Small Cell Lung Cancer",
    selected_source="abstract_title",
    generated_indication="Non-Small Cell Lung Cancer",
    rules_retrieved=[
        RuleRetrieved(
            category="Disease",
            subcategories=["Abbreviations"],
            reason="To expand NSCLC abbreviation"
        )
    ],
    components_identified=[
        ComponentIdentified(
            component="NSCLC",
            type="Disease Abbreviation",
            normalized_form="Non-Small Cell Lung Cancer",
            rule_applied="NSCLC -> Non-Small Cell Lung Cancer"
        )
    ]
)

MOCK_INDICATION_EXTRACTION_WITH_GENE = ExtractionLLMResponse(
    reasoning="Found 'EGFR-mutant NSCLC' - contains gene mutation (EGFR) and disease (NSCLC)",
    selected_source="abstract_title",
    generated_indication="EGFR-mutant Non-Small Cell Lung Cancer",
    rules_retrieved=[
        RuleRetrieved(
            category="Gene type",
            subcategories=["EGFR"],
            reason="To handle EGFR gene mutation formatting"
        ),
        RuleRetrieved(
            category="Disease",
            subcategories=["Abbreviations"],
            reason="To expand NSCLC abbreviation"
        )
    ],
    components_identified=[
        ComponentIdentified(
            component="EGFR-mutant",
            type="Gene Mutation",
            normalized_form="EGFR-mutant",
            rule_applied="Gene mutation formatting rule"
        ),
        ComponentIdentified(
            component="NSCLC",
            type="Disease Abbreviation",
            normalized_form="Non-Small Cell Lung Cancer",
            rule_applied="NSCLC -> Non-Small Cell Lung Cancer"
        )
    ]
)

MOCK_INDICATION_EXTRACTION_NO_INDICATION = ExtractionLLMResponse(
    reasoning="No medical indication found in abstract or session title",
    selected_source="none",
    generated_indication="",
    rules_retrieved=[],
    components_identified=[]
)

MOCK_INDICATION_EXTRACTION_SESSION_TITLE = ExtractionLLMResponse(
    reasoning="Abstract title unclear, using session title which mentions 'Melanoma'",
    selected_source="session_title",
    generated_indication="Melanoma",
    rules_retrieved=[],
    components_identified=[
        ComponentIdentified(
            component="Melanoma",
            type="Disease",
            normalized_form="Melanoma",
            rule_applied="Direct disease name"
        )
    ]
)

MOCK_INDICATION_EXTRACTION_COMPLEX = ExtractionLLMResponse(
    reasoning="Complex indication with multiple components: BRAF V600E mutant melanoma",
    selected_source="abstract_title",
    generated_indication="BRAF V600E-mutant Melanoma",
    rules_retrieved=[
        RuleRetrieved(
            category="Gene type",
            subcategories=["BRAF"],
            reason="To handle BRAF V600E mutation formatting"
        )
    ],
    components_identified=[
        ComponentIdentified(
            component="BRAF V600E",
            type="Gene Mutation",
            normalized_form="BRAF V600E-mutant",
            rule_applied="BRAF V600E mutation rule"
        ),
        ComponentIdentified(
            component="melanoma",
            type="Disease",
            normalized_form="Melanoma",
            rule_applied="Capitalize disease name"
        )
    ]
)


# =============================================================================
# Sample Rules Data for Testing
# =============================================================================

SAMPLE_RULES_DATA = [
    {
        "ID": "1",
        "Category": "Disease",
        "Sub Category": "Abbreviations",
        "Keyword": "NSCLC",
        "Do/Don't": "Do",
        "Generated_Rule": "Expand NSCLC to Non-Small Cell Lung Cancer"
    },
    {
        "ID": "2",
        "Category": "Gene type",
        "Sub Category": "EGFR",
        "Keyword": "EGFR",
        "Do/Don't": "Do",
        "Generated_Rule": "Keep EGFR as-is, add -mutant suffix if mutation mentioned"
    },
    {
        "ID": "3",
        "Category": "Gene type",
        "Sub Category": "BRAF",
        "Keyword": "BRAF",
        "Do/Don't": "Do",
        "Generated_Rule": "For BRAF V600E, format as 'BRAF V600E-mutant'"
    },
    {
        "ID": "4",
        "Category": "Common Check points",
        "Sub Category": "General",
        "Keyword": "metastatic",
        "Do/Don't": "Do",
        "Generated_Rule": "Include 'metastatic' qualifier in indication"
    },
    {
        "ID": "5",
        "Category": "Disease",
        "Sub Category": "Abbreviations",
        "Keyword": "RCC",
        "Do/Don't": "Do",
        "Generated_Rule": "Expand RCC to Renal Cell Carcinoma"
    }
]
