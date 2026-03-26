"""Mock LLM responses for drug class testing."""
from src.agents.drug_class.schemas import (
    DrugSelectionResult,
    RegimenLLMResponse,
    ExplicitLLMResponse,
    ExplicitExtractionDetail,
    ConsolidationLLMResponse,
    RefinedExplicitClasses,
    RemovedClassInfo,
)


# =============================================================================
# Step 1: Regimen Identification Mock Responses
# =============================================================================

MOCK_REGIMEN_FOLFOX = RegimenLLMResponse(
    components=["Fluorouracil", "Leucovorin", "Oxaliplatin"]
)

MOCK_REGIMEN_FOLFIRI = RegimenLLMResponse(
    components=["Fluorouracil", "Leucovorin", "Irinotecan"]
)

MOCK_REGIMEN_SINGLE_DRUG = RegimenLLMResponse(
    components=["Pembrolizumab"]
)

MOCK_REGIMEN_COMBINATION = RegimenLLMResponse(
    components=["Nivolumab", "Ipilimumab"]
)

MOCK_REGIMEN_EMPTY = RegimenLLMResponse(
    components=[]
)


# =============================================================================
# Step 4: Explicit Extraction Mock Responses
# =============================================================================

MOCK_EXPLICIT_PD1_DETAIL = ExplicitExtractionDetail(
    drug_class="PD-1 Inhibitor",
    evidence="Title explicitly mentions 'PD-1 Inhibitors'",
    confidence_score=0.95,
)

MOCK_EXPLICIT_PD1 = ExplicitLLMResponse(
    reasoning="The title explicitly mentions 'PD-1 Inhibitors' as a drug class",
    drug_classes=["PD-1 Inhibitor"],
    extraction_details=[MOCK_EXPLICIT_PD1_DETAIL],
)

MOCK_EXPLICIT_MULTIPLE = ExplicitLLMResponse(
    reasoning="Title mentions both PD-1 and CTLA-4 inhibitors",
    drug_classes=["PD-1 Inhibitor", "CTLA-4 Inhibitor"],
    extraction_details=[],
)

MOCK_EXPLICIT_NO_CLASSES = ExplicitLLMResponse(
    reasoning="No explicit drug class mentions found in the title",
    drug_classes=[],
    extraction_details=[],
)

MOCK_EXPLICIT_MONOCLONAL = ExplicitLLMResponse(
    reasoning="Title mentions 'Monoclonal Antibodies'",
    drug_classes=["Monoclonal Antibody"],
    extraction_details=[],
)


# =============================================================================
# Step 3: Selection Mock Responses
# =============================================================================

MOCK_SELECTION_SINGLE_CLASS = DrugSelectionResult(
    drug_name="Pembrolizumab",
    selected_drug_classes=["PD-1 Inhibitor"],
    reasoning="Only one class type (MoA) was found. Selected PD-1 Inhibitor.",
)

MOCK_SELECTION_MULTIPLE_MoA_WINS = DrugSelectionResult(
    drug_name="Pembrolizumab",
    selected_drug_classes=["PD-1 Inhibitor"],
    reasoning="Multiple class types found. MoA (PD-1 Inhibitor) takes priority over Therapeutic (Immunotherapy).",
)

MOCK_SELECTION_CHEMICAL_WINS = DrugSelectionResult(
    drug_name="Carboplatin",
    selected_drug_classes=["Platinum Compound"],
    reasoning="Chemical class (Platinum Compound) takes priority over Therapeutic (Chemotherapy Agent).",
)

MOCK_SELECTION_MULTI_TARGET = DrugSelectionResult(
    drug_name="Nivolumab + Ipilimumab",
    selected_drug_classes=["PD-1 Inhibitor", "CTLA-4 Inhibitor"],
    reasoning="Combination therapy with multiple biological targets. Both classes are relevant.",
)

MOCK_SELECTION_NO_CLASSES = DrugSelectionResult(
    drug_name="Unknown Drug",
    selected_drug_classes=["NA"],
    reasoning="No drug classes were extracted.",
)

MOCK_SELECTION_THERAPEUTIC_ONLY = DrugSelectionResult(
    drug_name="Aspirin",
    selected_drug_classes=["Analgesic"],
    reasoning="Only Therapeutic class type found.",
)


# =============================================================================
# Sample Extraction Details for Step 3 Input
# =============================================================================

SAMPLE_EXTRACTION_DETAILS_SINGLE = [
    {
        "extracted_text": "PD-1 Inhibitor",
        "normalized_form": "PD-1 Inhibitor",
        "class_type": "MoA",
        "evidence": "Pembrolizumab is a PD-1 blocking antibody",
        "source": "https://www.drugs.com/pembrolizumab.html",
        "rules_applied": ["rule_moa_identification"],
    }
]

SAMPLE_EXTRACTION_DETAILS_MULTIPLE = [
    {
        "extracted_text": "PD-1 Inhibitor",
        "normalized_form": "PD-1 Inhibitor",
        "class_type": "MoA",
        "evidence": "Pembrolizumab blocks PD-1 receptor",
        "source": "https://example.com/1",
        "rules_applied": ["rule_moa"],
    },
    {
        "extracted_text": "Immunotherapy",
        "normalized_form": "Immunotherapy",
        "class_type": "Therapeutic",
        "evidence": "Used in cancer immunotherapy",
        "source": "https://example.com/2",
        "rules_applied": ["rule_therapeutic"],
    },
    {
        "extracted_text": "Monoclonal Antibody",
        "normalized_form": "Monoclonal Antibody",
        "class_type": "Chemical",
        "evidence": "Pembrolizumab is a monoclonal antibody",
        "source": "https://example.com/3",
        "rules_applied": ["rule_chemical"],
    },
]

SAMPLE_EXTRACTION_DETAILS_COMBINATION = [
    {
        "extracted_text": "PD-1 Inhibitor",
        "normalized_form": "PD-1 Inhibitor",
        "class_type": "MoA",
        "evidence": "Nivolumab blocks PD-1",
        "source": "https://example.com/1",
        "rules_applied": [],
    },
    {
        "extracted_text": "CTLA-4 Inhibitor",
        "normalized_form": "CTLA-4 Inhibitor",
        "class_type": "MoA",
        "evidence": "Ipilimumab blocks CTLA-4",
        "source": "https://example.com/2",
        "rules_applied": [],
    },
]

SAMPLE_EXTRACTION_DETAILS_EMPTY = []


# =============================================================================
# Selection Input Data
# =============================================================================

SELECTION_INPUT_SINGLE_CLASS = {
    "abstract_id": 123,
    "abstract_title": "Study of Pembrolizumab in NSCLC",
    "drug_name": "Pembrolizumab",
    "extraction_details": SAMPLE_EXTRACTION_DETAILS_SINGLE,
}

SELECTION_INPUT_MULTIPLE_CLASSES = {
    "abstract_id": 123,
    "abstract_title": "Study of Pembrolizumab in NSCLC",
    "drug_name": "Pembrolizumab",
    "extraction_details": SAMPLE_EXTRACTION_DETAILS_MULTIPLE,
}

SELECTION_INPUT_NO_CLASSES = {
    "abstract_id": 456,
    "abstract_title": "Unknown Drug Study",
    "drug_name": "Unknown Drug",
    "extraction_details": SAMPLE_EXTRACTION_DETAILS_EMPTY,
}

SELECTION_INPUT_COMBINATION = {
    "abstract_id": 789,
    "abstract_title": "Nivolumab Plus Ipilimumab in Melanoma",
    "drug_name": "Nivolumab + Ipilimumab",
    "extraction_details": SAMPLE_EXTRACTION_DETAILS_COMBINATION,
}


# =============================================================================
# Step 5: Consolidation Mock Responses
# =============================================================================

MOCK_CONSOLIDATION_WITH_REMOVALS = ConsolidationLLMResponse(
    reasoning="Removed duplicates and parent classes. PD-1 Inhibitor is already captured in drug selections.",
    refined_explicit_drug_classes=RefinedExplicitClasses(
        drug_classes=["Immunotherapy"],
        removed_classes=[
            RemovedClassInfo(class_name="PD-1 Inhibitor", reason="Duplicate - already in drug selections"),
            RemovedClassInfo(class_name="Monoclonal Antibody", reason="Parent class - more specific class available"),
        ],
    ),
)

MOCK_CONSOLIDATION_NO_REMOVALS = ConsolidationLLMResponse(
    reasoning="All explicit classes are unique and specific.",
    refined_explicit_drug_classes=RefinedExplicitClasses(
        drug_classes=["PD-1 Inhibitor", "Immunotherapy"],
        removed_classes=[],
    ),
)

MOCK_CONSOLIDATION_ALL_REMOVED = ConsolidationLLMResponse(
    reasoning="All explicit classes were duplicates of drug-specific selections.",
    refined_explicit_drug_classes=RefinedExplicitClasses(
        drug_classes=[],
        removed_classes=[
            RemovedClassInfo(class_name="PD-1 Inhibitor", reason="Duplicate"),
            RemovedClassInfo(class_name="CTLA-4 Inhibitor", reason="Duplicate"),
        ],
    ),
)

MOCK_CONSOLIDATION_PARTIAL_REMOVALS = ConsolidationLLMResponse(
    reasoning="Removed generic parent class, kept specific classes.",
    refined_explicit_drug_classes=RefinedExplicitClasses(
        drug_classes=["PD-1 Inhibitor", "CTLA-4 Inhibitor"],
        removed_classes=[
            RemovedClassInfo(class_name="Immunotherapy", reason="Parent class - more specific classes available"),
        ],
    ),
)
