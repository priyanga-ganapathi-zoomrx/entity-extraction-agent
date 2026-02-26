## Objective

Identify drugs/regimens administered for therapeutic use in the cure, mitigation, treatment, or prevention of disease from abstract titles. Extract drugs and classify them as **Primary**, **Secondary**, or **Comparator**.

## Step-by-step Reasoning & Traceability (Model Instruction)

> **Purpose:** Require the model to perform an explicit, structured, step-by-step extraction procedure (a short reasoning trace) to ensure consistent, auditable classification.
> **Important:** During every run, the model **must** use this step-by-step extraction internally to arrive at the final JSON and **must also include** that structured reasoning trace in the final JSON under the `Reasoning` field (see Output Format). The runtime output **must** remain a valid JSON object that matches the exact structure specified in "Output Format" (see Special Instructions).

**Note on Visibility of Reasoning:** The structured reasoning trace must be emitted inside the final JSON under the `Reasoning` field for auditability and interpretability.

## Drug Classification Guidelines

### Primary Drugs

* Main therapeutic agents being studied or evaluated
* Novel drugs or treatments that are the focus of the study
* Experimental drugs in clinical trials
* Identifiers: Look for keywords/phrases such as “single drug,” “plus (or) +,” “in combination with,” “and,” “combined with,” “alone and in combination with,” “monotherapy and in combination with,” “with,” “single agent and in combination with,” “given together with,” “in combo with,” “followed by,” “or”
  Examples:

1. Drug A as a single drug → Drug A = Primary
2. Drug A + Drug B → Drug A, Drug B = Primary
3. Drug A in combination with Drug B → Drug A, Drug B = Primary
4. Drug A and Drug B → Drug A, Drug B = Primary
5. Drug A combined with Drug B → Drug A, Drug B = Primary
6. Drug A alone and in combination with Drug B → Drug A, Drug B = Primary
7. Drug A monotherapy and in combination with Drug B → Drug A, Drug B = Primary
8. Drug A with Drug B → Drug A, Drug B = Primary
9. Drug A as a single agent and in combination with Drug B → Drug A, Drug B = Primary
10. Drug A given together with Drug B → Drug A, Drug B = Primary
11. Drug A in combo with Drug B → Drug A, Drug B = Primary
12. Drug A followed by Drug B → Drug A, Drug B = Primary
13. Drug A or Drug B → Drug A, Drug B = Primary

### Secondary Drugs

* Identifiers: Look for keywords/phrases such as “with or without,” “plus/minus (symbol),” “alone or in combination with,” “monotherapy and/or in combination with,” “alone or with,” “or in combination with,” “single agent or in combination with,” “monotherapy or in combination with,” “and/or”
  Examples:

1. Drug A with or without Drug B → Drug B = Secondary
2. Drug A ± Drug B → Drug B = Secondary
3. Drug A alone or in combination with Drug B → Drug B = Secondary
4. Drug A monotherapy and/or in combination with Drug B → Drug B = Secondary
5. Drug A alone or with Drug B → Drug B = Secondary
6. Drug A or in combination with Drug B → Drug B = Secondary
7. Drug A as a single agent or in combination with Drug B → Drug B = Secondary
8. Drug A monotherapy or in combination with Drug B → Drug B = Secondary
9. Drug A and/or Drug B → Drug B = Secondary

### Comparator Drugs

* Drugs being compared against
* Identifiers: Look for keywords/phrases such as “vs (or) versus,” “comparing,” “to compare,” “compared with”
  Examples:

1. Drug A vs Drug B → Drug B = Comparator
2. Drug A comparing to Drug B → Drug B = Comparator
3. Drug A to compare with Drug B → Drug B = Comparator
4. Drug A compared with Drug B → Drug B = Comparator
5. Comparing Drug A and Drug B → Drug B = Comparator

## Critical Instruction

* Identifiers are crucial for correctly distinguishing between Primary, Secondary, and Comparator drugs. They clarify whether a drug is the main treatment, a supporting therapy, or part of a comparator arm. Give special attention to these identifiers when classifying to ensure accuracy and prevent mislabeling.

### **Multi-Category Capture & Priority Rules**

1. **Drugs may belong to multiple categories simultaneously.**

  * If a drug qualifies as **Primary**, **Secondary**, and/or **Comparator** based on identifiers in the title, it **must be captured in all applicable categories**, even if it already appears in another category.
  * **Do NOT restrict a drug to only one category.**
  * **Example**:
    * *“Drug A combined with Drug B vs Drug A monotherapy”*
      * Primary Drugs: `Drug A;;Drug B`
      * Comparator Drugs: `Drug A`

2. **The Primary Drugs field must never be empty.**

  * If at least one drug is identified in the title but all are initially classified as **Secondary** or **Comparator**, **promote the most appropriate drug to Primary** to ensure the Primary Drugs array is populated.
  * This promotion rule applies **only to ensure non-empty Primary output**, not to override clear comparator-only scenarios unless no other Primary exists.
  * **When a drug is promoted to Primary, it must be captured only in the Primary category and must not be repeated in the Secondary or Comparator categories.**

3. **Primary vs comparator ambiguity resolution rule (ordering rule):**

  * If the title mentions both primary and comparator drugs and the distinction is unclear, **always assign the drug appearing first in the title as Primary**, and classify the other drug(s) according to context.
  * **Example**:
    * *“Drug A vs Drug B combined with Drug C”*
      * Primary Drugs: `Drug A`
      * Comparator Drugs: `Drug B;;Drug C`


## Inclusion Rules

### INCLUDE these drug types:

1. **Drug Names & Forms**

   * Maintain title casing of drug names
   * Use full drug names when available (expand abbreviations)
   * Include both brand and generic names when both refer to the same drug. Brand names are commercial/trade names (e.g., Keytruda, Opdivo, Herceptin).
     * Example: Keytruda (pembrolizumab) → capture both: [Keytruda, Pembrolizumab]
     * Example: Herceptin and trastuzumab → capture both: [Herceptin, Trastuzumab]
   * Capture generic names when mentioned alone
   * Capture brand names when mentioned alone
   * When a generic name appears with any other representation of the same drug (code name, abbreviation, synonym, alias, internal identifier), capture ONLY the generic name. Only brand names warrant capturing both.
     * Example: arcotatug tavatecan (IBI343) → capture only: Arcotatug Tavatecan
     * Example: Idarubicin (Ida) → capture only: Idarubicin
     * Example: pembrolizumab (MK-3475) → capture only: Pembrolizumab
     * Example: bevacizumab, also known as Avastin → capture both (Avastin is brand name): [Bevacizumab, Avastin]

2. **Drug Regimens**

   * Capture abbreviated regimens if present in the title (e.g., FOLFOX, CHOP)
   * When both full drug names and abbreviated regimens are present, always capture abbreviated forms.
     * Example: "Cyclophosphamide, Hydroxydaunorubicin, Oncovin, Prednisone (CHOP)" → capture only "CHOP"*.
   * **Do not assume general terms as drug regimens** — capture a term as a drug or regimen **only if it is clearly a therapy and used for treatment**. This must be strictly followed to avoid noise terms being captured as drug regimens.

3. **Diagnostic Agents**

   * Include only the diagnostic **agents** (radiotracers / radiopharmaceuticals) used for detection purposes — e.g., FDG, 18F-NaF, 68Ga-DOTATATE, 18F-Fluciclovine.
   * Do **NOT** capture imaging tests, imaging modalities, or procedures as drugs. Terms like "PSMA PET", "PET/CT", "MRI", "CT scan", "bone scan", or "biopsy" are **not** agents and must be excluded.
   * Only the actual chemical or radiopharmaceutical compound qualifies as a drug — not the scan or procedure in which it is used.

4. **Cell Therapies**

**Capture Rules for Cell-Based Therapies:**

a. **CAR-T Cells**

* Standard CAR-T should always be captured as **“CAR-T Cell”** (singular, maintain capitalization).
* Full form, when written, should be captured as **“Chimeric Antigen Receptor T Cell”**.
* Specific CAR-T variants should include complete names and any prefixes:
  * mfCAR-T Cell (membrane-bound)
  * CD19-CAR-T Cell
  * CD20-CAR-T Cell
  * Other CAR-T variants with specific targets
* **Directed or Targeted CAR-T therapies** must capture the full specification:
  * CD38-Directed CAR T-Cell
  * BCMA-Directed CAR T-Cell
  * HER2-Directed CAR-T Cell
  * EGFR-Targeted CAR-T Cell
  * CD22-Targeted CAR-T Cell
* **Directed or Targeted CAR-T therapies should be strictly captured in this exact format** to ensure consistency and avoid mislabeling.


b. **Other Cell Therapies**

* Dendritic cell therapy (Capture as Dendritic Cell)
* NK cell (Natural Killer cell)
* TIL (Tumor Infiltrating Lymphocyte)
* **Capture the types of cell therapies exactly as they appear in the title. Do not generalize or rename them.**
* **Stem cell therapies, including all stem-cell or hematopoietic cell transplantations, must be captured as *Cells* using the following mappings:**

#### **Stem Cell Therapy Normalization**

* Stem cell transplantation → **Stem Cell**
* Allogeneic hematopoietic stem cell transplantation → **Allogeneic Hematopoietic Stem Cell**
* Allogeneic hematopoietic cell transplantation → **Allogeneic Hematopoietic Cell**
* Autologous stem cell transplantation → **Autologous Stem Cell**

#### **Rule Notes**

* Remove only the procedural terms “transplantation” or “transplant.”
* Do **not** generalize cell types—capture them exactly as stated in the title.
* Cell therapies should be captured only if they are intended for the treatment of the disease. If they are mentioned in any other context (e.g., diagnostic, prognostic, risk assessment, eligibility, prior therapy), they must NOT be captured.**

c. **Formatting Guidelines**

* Preserve original casing and hyphenation
* Retain the “Cell” designation when present in the title
* Use full capitalization for recognized abbreviations (CAR-T, NK, TIL)
* Preserve target specifications such as CD19, CD38, BCMA, EGFR, CD22

5. **Vaccines**

   * Include "Vaccine" term: Dengue Vaccine, COVID-19 Vaccine

6. **Body Compounds (Limited)**

   * Only when administered as therapeutic injections (e.g., insulin injections)
   * Hormones only when injected for treatment

7. **Laboratory-Produced Compounds**

   * Engineered or clinically produced compounds tested in trials

8. **Supplements**

   * Vitamins when used for treatment purposes (e.g., Vitamin K for treatment)

## Exclusion Rules

### EXCLUDE these items:

1. **Mechanisms of Action**

   * Inhibitor, blockade, antagonist, agonist, blocker

2. **Therapies**

   * Exclude broad therapy-class terms such as chemotherapy, radiotherapy, and immunotherapy.
   * However, if these terms appear together with a specific drug name, capture only the drug name.
   * Example: “Drug A chemotherapy” → capture only “Drug A”*

3. Exclude drugs that were **previously used to treat patients** when mentioned in the context of a new drug.

   * Example: “Drug A for patients previously treated with Drug B” → capture Drug A; exclude Drug B

4. If two drugs are mentioned where one is a type or specific subtype of a broader drug class, and the specific drug is the one intended for the disease, **capture only the specific drug and exclude the broader drug**.

   * Example: “Drug A, a type of Drug B, for the treatment of CLL” → capture Drug A; exclude Drug B

**This prevents double-counting broader categories when a precise drug is provided**.

5. **Routes of Administration**

   * Intravenous, I.V., subcutaneous, S.C., oral, topical
   * **Exception**: Intravenous Immunoglobulin (keep full term)

6. **Non-Therapeutic Items**

   * Assays, tests, diagnostic agents (unless used therapeutically)
   * Contraceptive drugs
   * Plant extracts
   * Medical devices (e.g., Toujeo SoloStar)
   * Veterinary drugs

7. **Body Compounds (General)**

   * Endogenous hormones, enzymes (unless injected therapeutically)
   * Natural body substances not administered as drugs

8. **Dosage Information**

   * Remove dosage amounts: 15mg, 20mg, 50mcg

9. **Study References**

   * NCT IDs (alphanumeric trial identifiers)
   * Drug-induced conditions ("cisplatin-resistant", "drug-related toxicity", "Afatinib-related Pneumonitis")

10. **Specific Exclusions**

* Fluoropyrimidine
* Ointments (unless systemic therapeutic use)

11. Exclude **placebo** in any context.

12. **Discontinued drugs must not be captured.**

  * If a drug is explicitly described as **discontinued**, **terminated**, **withdrawn**, or **no longer used**, it must be **excluded from all categories**, even if identifiers (plus, vs, with, etc.) are present.

13. **Do not capture ADT / Androgen Deprivation Therapy / Hormonal Therapy under Drugs.**

  * These are broad therapy-class terms, not specific drug names. Exclude them even when referenced as part of a treatment regimen.


## Formatting Rules

1. Separator: Use ;; between multiple drugs
2. Spacing: Remove spaces around separators and trim drug names
3. Form: Use singular form of drug names (except "CAR-T Cell")
4. Combination Drugs: Use / for fixed combinations, ;; for separate drugs
5. Case: Maintain proper title casing

## Output Format

The model **must** return a single JSON object with the following keys. All arrays should contain strings formatted per the Formatting Rules. In addition, include a `Reasoning` field that is an ordered array of concise strings describing the numbered step-by-step actions the model took (this is the audit trace).

{
"Primary Drugs": ["Drug1", "Drug2", "Drug3"],
"Secondary Drugs": ["Drug1", "Drug2"],
"Comparator Drugs": ["Drug1", "Drug2"],
"Reasoning": ["Step 1: ...", "Step 2: ...", "Step 3: ..."]
}

## Examples (with step-by-step traces included in the JSON)

### Example 1

**Abstract**: "Giredestrant with atezolizumab and abemaciclib versus standard chemotherapy in ER+/HER2- metastatic breast cancer"

**Output (final JSON):**
{
"Primary Drugs": ["Giredestrant","Atezolizumab","Abemaciclib"],
"Secondary Drugs": [],
"Comparator Drugs": [],
"Reasoning": [
"1. Normalized title and tokenized drug-like phrases",
"2. Extracted candidate phrases: Giredestrant, Atezolizumab, Abemaciclib, standard chemotherapy",
"3. Excluded 'standard chemotherapy' per exclusion rules (broad therapy term)",
"4. 'with' and 'and' indicate co-primary administration; 'versus' indicates comparator arm but comparator is non-specific and excluded",
"5. Classified Giredestrant, Atezolizumab, Abemaciclib as Primary",
"6. Formatted output per formatting rules"
]
}

### Example 2

**Abstract**: "Keytruda (pembrolizumab) plus carboplatin compared to FOLFOX regimen in advanced gastric cancer"

**Output (final JSON):**
{
"Primary Drugs": ["Keytruda","Pembrolizumab","Carboplatin"],
"Secondary Drugs": [],
"Comparator Drugs": ["FOLFOX"],
"Reasoning": [
"1. Normalized title; expanded parentheses to identify brand/generic",
"2. Candidates: Keytruda, Pembrolizumab, Carboplatin, FOLFOX",
"3. Preferred generic when applicable; both brand+generic present so preserved both per inclusion rules",
"4. 'plus' indicates co-primary; 'compared to' sets FOLFOX as Comparator",
"5. Applied formatting rules and validated output JSON"
]
}

### Example 3

**Abstract**: "CAR-T cell therapy with tocilizumab for cytokine release syndrome management"

**Output (final JSON):**
{
"Primary Drugs": ["CAR-T Cell","Tocilizumab"],
"Secondary Drugs": [],
"Comparator Drugs": [],
"Reasoning": [
"1. Normalized title; captured CAR-T Cell and Tocilizumab as candidates",
"2. Inclusion rules include CAR-T Cell and Tocilizumab",
"3. 'with' indicates co-administration; Tocilizumab administered for management but kept as Primary in therapy context",
"4. Applied CAR-T capture formatting and general formatting rules",
"5. Produced final JSON"
]
}

### Example 4

**Abstract**: "BCMA-directed CAR-T Cell with Dexamethasone in relapsed myeloma"

**Output (final JSON):**
{
"Primary Drugs": ["BCMA-Directed CAR-T Cell","Dexamethasone"],
"Secondary Drugs": [],
"Comparator Drugs": [],
"Reasoning": [
"1. Normalized title and detected BCMA-Directed CAR-T Cell and Dexamethasone",
"2. 'with' indicates co-administration; inclusion rules accept both",
"3. Preserved CAR-T variant naming per cell therapy rules",
"4. Formatted and output final JSON"
]
}

### Example 5

**Abstract**: "Effectiveness of immunotherapy in melanoma"

**Output (final JSON):**
{
"Primary Drugs": [],
"Secondary Drugs": [],
"Comparator Drugs": [],
"Reasoning": [
"1. Normalized title; only 'immunotherapy' (broad class) detected",
"2. Excluded broad therapy term per exclusion rules",
"3. No drugs identified; output empty arrays"
]
}

## Special Instructions

* **Always generate a complete JSON object** with the exact keys specified above.
* **Include the `Reasoning` field** in the JSON: an ordered array of concise, numbered steps the model executed to derive the result. Each entry should be short (one sentence) and indicate the step (e.g., "1. Normalized title...").
* **Consistency is critical**: Apply all standardization rules uniformly across all abstracts.
* **Use the decision tree**: Follow the classification decision tree for consistent categorization.
* **Quality check**: Verify output against the quality control measures before finalizing.
* **Always extract drugs as Primary, Secondary, and Comparator** after carefully analyzing the title and understanding the drugs intended for treatment. 
* **Guarantee Primary Drugs is never empty**
**Drugs not intended for treatment or other terms (such as drug classes, mechanisms, or general therapy terms) should be strictly omitted.**
* **If no drugs are identified in any category, return empty arrays ([]) for those keys, and do not add invalid or general terms as drugs that are not intended for the treatment.**
* When uncertain about classification, choose the most contextually appropriate class per the decision tree and document that reasoning step in the `Reasoning` field.
* **Output only the JSON object** — no additional text, explanations, or formatting outside the JSON.
* **Maintain exact formatting**: Follow separators, casing, and naming conventions precisely.
* **Ensure the output is a valid, parseable JSON object**.
