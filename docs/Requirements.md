# Functional Requirements

## FR-1
System shall generate CFA Level I videos from official curriculum and Schweser notes.

## FR-2
Each book shall produce 3–6 hours of video content.

## FR-3
All factual claims must be traceable to source documents.

## FR-4
Formulas must have:
- LaTeX display
- Natural spoken Chinese explanation

## FR-5
System must support resuming interrupted runs.

---

# Non-Functional Requirements

## NFR-1
No hallucinated facts allowed.

## NFR-2
Cost per book must be bounded and predictable.

## NFR-3
System must be auditable.

## NFR-4
Local GPU must be utilized for TTS and video rendering.

## NFR-5
Cloud dependency limited to LLM reasoning and vision extraction.
